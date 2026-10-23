/* SPDX-License-Identifier: Apache-2.0
 * Independent hardware pulse train. P1.03 starts it once; P1.04 toggles.
 * No per-edge CPU service, radio, PMIC, UICR or clock-sharing wire.
 * Counter COMPARE stops the source in hardware at exactly the requested count.
 */
#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/ring_buffer.h>
#include <zephyr/drivers/clock_control/nrf_clock_control.h>
#include <zephyr/sys/onoff.h>
#include <nrfx_clock.h>
#include <gpiote_nrfx.h>
#include <nrfx_gpiote.h>
#include <nrfx_timer.h>
#include <helpers/nrfx_gppi.h>
#include <hal/nrf_gpio.h>
#include <stdio.h>
#include <string.h>

#define VERSION "async-source-v2"
#define INPUT_PIN NRF_GPIO_PIN_MAP(1,3)
#define MARKER_PIN NRF_GPIO_PIN_MAP(1,4)
#define GPIOTE_NODE DT_NODELABEL(gpiote20)
static nrfx_gpiote_t *const io = &GPIOTE_NRFX_INST_BY_NODE(GPIOTE_NODE);
static nrfx_timer_t source = NRFX_TIMER_INSTANCE(NRF_TIMER20);
static nrfx_timer_t stamp = NRFX_TIMER_INSTANCE(NRF_TIMER21);
static nrfx_timer_t inputs = NRFX_TIMER_INSTANCE(NRF_TIMER22);
static nrfx_timer_t edges = NRFX_TIMER_INSTANCE(NRF_TIMER23);
static nrfx_gppi_handle_t start_conn,edge_conn,first_conn,stop_conn;
static uint8_t in_ch,out_ch;
static const struct device *const console=DEVICE_DT_GET(DT_CHOSEN(zephyr_console));
RING_BUF_DECLARE(commands,256);
static atomic_t armed,complete,errors,dropped;
static uint32_t run_id,requested,period_ticks,cmd_overflow;
static volatile bool done_notice;
static struct onoff_client hf_client;
static bool hf_before_request;

static bool hfxo_running(void)
{
    nrf_clock_hfclk_t src;
    return nrfx_clock_is_running(NRF_CLOCK_DOMAIN_HFCLK,&src)&&src==NRF_CLOCK_HFCLK_HIGH_ACCURACY;
}
static int request_hfxo(void)
{
    hf_before_request=hfxo_running();
    struct onoff_manager *mgr=z_nrf_clock_control_get_onoff(CLOCK_CONTROL_NRF_SUBSYS_HF);
    if(!mgr)return -ENODEV;
    sys_notify_init_spinwait(&hf_client.notify);
    int rc=onoff_request(mgr,&hf_client);if(rc<0)return rc;
    int result=0;int64_t deadline=k_uptime_get()+2000;
    do {
        rc=sys_notify_fetch_result(&hf_client.notify,&result);
        if(rc!=-EAGAIN)break;
        if(k_uptime_get()>=deadline)return -ETIMEDOUT;
        k_sleep(K_MSEC(1));
    }while(true);
    if(rc)return rc;
    if(result)return result;
    return hfxo_running()?0:-EIO;
}

/* CC0/CC1 on the edge counter are compare thresholds; never capture into them. */
static uint32_t count(nrfx_timer_t *t) { return nrfx_timer_capture(t,NRF_TIMER_CC_CHANNEL4); }
static void stop(void)
{
    nrfx_gppi_conn_disable(start_conn);nrfx_gppi_conn_disable(edge_conn);
    nrfx_gpiote_trigger_disable(io,INPUT_PIN);
    nrf_timer_task_trigger(source.p_reg,NRF_TIMER_TASK_STOP);atomic_clear(&armed);
}
static void end_handler(nrf_timer_event_t event,void *unused)
{
    ARG_UNUSED(unused);
    if(event!=NRF_TIMER_EVENT_COMPARE0)return;
    if(count(&edges)!=requested||count(&inputs)!=1)atomic_inc(&errors);
    /* Hardware stop has already happened via stop_conn. */
    stop();atomic_set(&complete,1);done_notice=true;
}
static void status(const char *prefix)
{
    printk("%s version=" VERSION " run=%u armed=%d complete=%d requested_edges=%u input_count=%u "
           "edge_count=%u period_ticks=%u timer_hz=16000000 marker_level=%u input_tick=%u "
           "first_counter_tick=%u last_edge_tick=%u stop_counter_tick=%u errors=%d uart_dropped=%d "
           "cmd_overflow=%u hf_before_request=%u hf_high_accuracy=%u input_pin=P1.03 marker_pin=P1.04\n",
           prefix,run_id,(int)atomic_get(&armed),(int)atomic_get(&complete),requested,
           count(&inputs),count(&edges),period_ticks,nrf_gpio_pin_read(MARKER_PIN),
           nrfx_timer_capture_get(&stamp,NRF_TIMER_CC_CHANNEL0),
           nrfx_timer_capture_get(&stamp,NRF_TIMER_CC_CHANNEL3),
           nrfx_timer_capture_get(&stamp,NRF_TIMER_CC_CHANNEL1),
           nrfx_timer_capture_get(&stamp,NRF_TIMER_CC_CHANNEL2),
           (int)atomic_get(&errors),(int)atomic_get(&dropped),cmd_overflow,hf_before_request?1u:0u,hfxo_running()?1u:0u);
}
static void process(char *line)
{
    if(!strcmp(line,"freeze")){stop();done_notice=false;status("AS_FROZEN");return;}
    if(!strcmp(line,"status")){status("AS_STATUS");return;}
    if(atomic_get(&armed)){printk("ERROR busy\n");return;}
    unsigned r,n,t;char extra;
    if(sscanf(line,"arm %u %u %u %c",&r,&n,&t,&extra)!=3||!r||r==run_id||n<2||n>960000||
       (n&1u)||(t!=16000&&t!=8000&&t!=4000&&t!=2000&&t!=2011)||
       (uint64_t)n*t>1920000000ull||!nrf_gpio_pin_read(INPUT_PIN)){
        printk("ERROR invalid_arm_or_input\n");return;
    }
    stop();unsigned key=irq_lock();
    run_id=r;requested=n;period_ticks=t;atomic_clear(&complete);atomic_clear(&errors);done_notice=false;
    for(unsigned i=0;i<4;++i){
        nrfx_timer_t *v=(nrfx_timer_t *[]){&source,&stamp,&inputs,&edges}[i];
        nrf_timer_task_trigger(v->p_reg,NRF_TIMER_TASK_STOP);nrf_timer_task_trigger(v->p_reg,NRF_TIMER_TASK_CLEAR);
    }
    nrfx_gpiote_out_task_force(io,MARKER_PIN,0);
    nrfx_timer_extended_compare(&source,NRF_TIMER_CC_CHANNEL0,t,NRF_TIMER_SHORT_COMPARE0_CLEAR_MASK,false);
    nrfx_timer_extended_compare(&edges,NRF_TIMER_CC_CHANNEL0,n,0,true);
    nrfx_timer_compare(&edges,NRF_TIMER_CC_CHANNEL1,1,false);
    nrf_timer_task_trigger(stamp.p_reg,NRF_TIMER_TASK_START);
    nrf_timer_task_trigger(inputs.p_reg,NRF_TIMER_TASK_START);nrf_timer_task_trigger(edges.p_reg,NRF_TIMER_TASK_START);
    nrfx_gppi_conn_enable(first_conn);nrfx_gppi_conn_enable(stop_conn);
    nrfx_gppi_conn_enable(edge_conn);nrfx_gppi_conn_enable(start_conn);
    atomic_set(&armed,1);nrfx_gpiote_trigger_enable(io,INPUT_PIN,false);
    irq_unlock(key);status("AS_ARMED");
}
static void console_irq(const struct device *dev,void *unused)
{
    ARG_UNUSED(unused);uart_irq_update(dev);
    while(uart_irq_rx_ready(dev)){
        uint8_t b[16];int n=uart_fifo_read(dev,b,sizeof(b));if(n<=0)break;
        uint32_t got=ring_buf_put(&commands,b,n);if(got<(uint32_t)n)atomic_add(&dropped,n-got);
    }
}
static int init(void)
{
    int rc=request_hfxo();if(rc)return rc;
    IRQ_CONNECT(DT_IRQN(GPIOTE_NODE),DT_IRQ(GPIOTE_NODE,priority),nrfx_gpiote_irq_handler,&GPIOTE_NRFX_INST_BY_NODE(GPIOTE_NODE),0);
    IRQ_CONNECT(NRFX_IRQ_NUMBER_GET(NRF_TIMER23),1,nrfx_timer_irq_handler,&edges,0);
    rc=nrfx_gpiote_init(io,0);if(rc)return rc;
    if((rc=nrfx_gpiote_channel_alloc(io,&in_ch)))return rc;
    if((rc=nrfx_gpiote_channel_alloc(io,&out_ch)))return rc;
    const nrf_gpio_pin_pull_t pull=NRF_GPIO_PIN_PULLUP;
    const nrfx_gpiote_trigger_config_t trig={.trigger=NRFX_GPIOTE_TRIGGER_HITOLO,.p_in_channel=&in_ch};
    const nrfx_gpiote_input_pin_config_t in={.p_pull_config=&pull,.p_trigger_config=&trig};
    if((rc=nrfx_gpiote_input_configure(io,INPUT_PIN,&in)))return rc;
    const nrfx_gpiote_output_config_t out={.drive=NRF_GPIO_PIN_S0S1,.input_connect=NRF_GPIO_PIN_INPUT_CONNECT,.pull=NRF_GPIO_PIN_NOPULL};
    const nrfx_gpiote_task_config_t task={.task_ch=out_ch,.polarity=NRF_GPIOTE_POLARITY_TOGGLE,.init_val=NRF_GPIOTE_INITIAL_VALUE_LOW};
    if((rc=nrfx_gpiote_output_configure(io,MARKER_PIN,&out,&task)))return rc;
    nrfx_gpiote_out_task_enable(io,MARKER_PIN);
    for(unsigned i=0;i<4;++i){
        nrfx_timer_t *v=(nrfx_timer_t *[]){&source,&stamp,&inputs,&edges}[i];
        nrfx_timer_config_t cfg=NRFX_TIMER_DEFAULT_CONFIG(16000000u);cfg.bit_width=NRF_TIMER_BIT_WIDTH_32;
        if(i>=2)cfg.mode=NRF_TIMER_MODE_COUNTER;
        if((rc=nrfx_timer_init(v,&cfg,i==3?end_handler:NULL)))return rc;
    }
    if((rc=nrfx_gppi_conn_alloc(nrfx_gpiote_in_event_address_get(io,INPUT_PIN),nrfx_timer_capture_task_address_get(&stamp,0),&start_conn)))return rc;
    if((rc=nrfx_gppi_ep_attach(nrfx_timer_task_address_get(&source,NRF_TIMER_TASK_CLEAR),start_conn)))return rc;
    if((rc=nrfx_gppi_ep_attach(nrfx_timer_task_address_get(&source,NRF_TIMER_TASK_START),start_conn)))return rc;
    if((rc=nrfx_gppi_ep_attach(nrfx_timer_task_address_get(&inputs,NRF_TIMER_TASK_COUNT),start_conn)))return rc;
    if((rc=nrfx_gppi_conn_alloc(nrfx_timer_compare_event_address_get(&source,0),nrfx_gpiote_out_task_address_get(io,MARKER_PIN),&edge_conn)))return rc;
    if((rc=nrfx_gppi_ep_attach(nrfx_timer_task_address_get(&edges,NRF_TIMER_TASK_COUNT),edge_conn)))return rc;
    if((rc=nrfx_gppi_ep_attach(nrfx_timer_capture_task_address_get(&stamp,1),edge_conn)))return rc;
    if((rc=nrfx_gppi_conn_alloc(nrfx_timer_compare_event_address_get(&edges,1),nrfx_timer_capture_task_address_get(&stamp,3),&first_conn)))return rc;
    if((rc=nrfx_gppi_conn_alloc(nrfx_timer_compare_event_address_get(&edges,0),nrfx_timer_task_address_get(&source,NRF_TIMER_TASK_STOP),&stop_conn)))return rc;
    if((rc=nrfx_gppi_ep_attach(nrfx_timer_capture_task_address_get(&stamp,2),stop_conn)))return rc;
    stop();return 0;
}
int main(void)
{
    if(!device_is_ready(console)){printk("BOOT_ERROR console\n");return 0;}
    int rc=init();if(!rc)rc=uart_irq_callback_user_data_set(console,console_irq,NULL);
    if(rc){printk("BOOT_ERROR rc=%d\n",rc);return 0;}
    uart_irq_rx_enable(console);status("AS_READY");char line[96];unsigned used=0;bool overflow=false;
    for(;;){
        uint8_t ch;
        while(ring_buf_get(&commands,&ch,1)){
            if(ch=='\r'||ch=='\n'){line[used]=0;if(overflow)++cmd_overflow;else if(used)process(line);used=0;overflow=false;}
            else if(used<sizeof(line)-1)line[used++]=ch;else overflow=true;
        }
        if(done_notice){status("AS_DONE");done_notice=false;}
        k_sleep(K_MSEC(1));
    }
}
