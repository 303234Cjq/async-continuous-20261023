// Local finite-duration continuous acquisition, no wireless or Nordic control.
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "pico/stdlib.h"
#include "hardware/clocks.h"
#include "hardware/dma.h"
#include "hardware/irq.h"
#include "hardware/sync.h"
#include "hardware/pio.h"
#include "hardware/regs/dma.h"
#include "hardware/structs/sio.h"
#include "finite.pio.h"

#define VERSION "continuous-input-v1"
#define CHANNELS 4u
#define WORDS 4096u
#define BLOCK_BYTES (WORDS*4u)
#define BLOCK_SAMPLES (WORDS*16u)
#define FS 1000000u
#define A 12u
#define B 13u
enum {ARMED=1,BUSY,READY,SENDING};
enum {ERR_ORDER=1u,ERR_BACKPRESSURE=2u,ERR_DMA=4u,ERR_TIMEOUT=8u,ERR_ABORT=16u};
static uint32_t buffers[CHANNELS][WORDS] __attribute__((aligned(16384)));
static volatile uint32_t ownership[CHANNELS],block_seq[CHANNELS],produced,exported,errors;
static volatile bool active;
static uint32_t requested,dma_mask,last_run,partial_discarded;
static int channels[CHANNELS];
static uint offset;
static PIO io=pio0;

static uint32_t crc32(const void *input,size_t length)
{
    const uint8_t *p=input;uint32_t crc=0xffffffffu;
    for(size_t i=0;i<length;++i){crc^=p[i];for(unsigned j=0;j<8;++j)crc=(crc>>1)^(0xedb88320u&(0u-(crc&1u)));}
    return ~crc;
}
static void release_a(void){gpio_put(A,0);gpio_set_dir(A,GPIO_IN);gpio_disable_pulls(A);}
static bool b_input(void){return !gpio_is_dir_out(B)&&!(io->dbg_padoe&(1u<<B));}
static void ready(void)
{
    printf("STREAM_READY version=" VERSION " idle=1 inputs=12,13 A_released=%u A_latch_high=%u B_input=%u "
           "local_pullup=%u sample_hz=1000000 words_per_block=4096 buffers=4\n",
           gpio_is_dir_out(A)?0u:1u,(sio_hw->gpio_out&(1u<<A))?1u:0u,b_input()?1u:0u,gpio_is_pulled_up(A)?1u:0u);
    fflush(stdout);
}
static void halt_capture(void)
{
    bool was_active=active;
    active=false;pio_sm_set_enabled(io,0,false);dma_set_irq0_channel_mask_enabled(dma_mask,false);
    if(was_active)partial_discarded=0;
    for(unsigned i=0;i<CHANNELS;++i){
        if(was_active&&dma_channel_is_busy(channels[i]))partial_discarded+=WORDS-dma_channel_hw_addr(channels[i])->transfer_count;
        dma_channel_abort(channels[i]);
    }
    dma_hw->ints0=dma_mask;
}
static void dma_irq(void)
{
    uint32_t pending=dma_hw->ints0&dma_mask;
    if(!active){dma_hw->ints0=pending;return;}
    while(pending&&active){
        unsigned slot=produced%CHANNELS;uint32_t bit=1u<<channels[slot];
        if(!(pending&bit)||ownership[slot]!=BUSY){errors|=ERR_ORDER;halt_capture();return;}
        dma_hw->ints0=bit;pending&=~bit;
        uint32_t ctrl=dma_channel_hw_addr(channels[slot])->ctrl_trig;
        if(ctrl&(DMA_CH0_CTRL_TRIG_READ_ERROR_BITS|DMA_CH0_CTRL_TRIG_WRITE_ERROR_BITS)){errors|=ERR_DMA;halt_capture();return;}
        block_seq[slot]=produced;__compiler_memory_barrier();ownership[slot]=READY;++produced;
        unsigned next=produced%CHANNELS;
        // The address ring prevents an unsafe out-of-buffer write even on failure.
        // Any reuse before export invalidates the ENTIRE run, including sent blocks.
        if(ownership[next]!=ARMED){errors|=ERR_BACKPRESSURE;halt_capture();return;}
        if(produced==requested){halt_capture();return;}
        ownership[next]=BUSY;
    }
}
static void prepare(void)
{
    halt_capture();pio_sm_clear_fifos(io,0);pio_sm_restart(io,0);
    pio_sm_config c=continuous_pair_program_get_default_config(offset);
    sm_config_set_in_pins(&c,A);sm_config_set_in_shift(&c,true,true,32);sm_config_set_fifo_join(&c,PIO_FIFO_JOIN_RX);
    sm_config_set_clkdiv_int_frac(&c,125,0);pio_sm_init(io,0,offset,&c);
    pio_sm_exec(io,0,pio_encode_mov(pio_isr,pio_null));
    for(unsigned i=0;i<CHANNELS;++i){
        dma_channel_config d=dma_channel_get_default_config(channels[i]);
        channel_config_set_transfer_data_size(&d,DMA_SIZE_32);channel_config_set_read_increment(&d,false);
        channel_config_set_write_increment(&d,true);channel_config_set_dreq(&d,pio_get_dreq(io,0,false));
        channel_config_set_chain_to(&d,channels[(i+1)%CHANNELS]);channel_config_set_ring(&d,true,14);
        channel_config_set_high_priority(&d,true);
        dma_channel_configure(channels[i],&d,buffers[i],&io->rxf[0],WORDS,false);
        ownership[i]=ARMED;block_seq[i]=0xffffffffu;
    }
    produced=exported=errors=0;partial_discarded=0;io->fdebug=0xffffffffu;dma_hw->ints0=dma_mask;
}
static void run(uint32_t id,uint32_t blocks,uint32_t hold_ms)
{
    requested=blocks;prepare();release_a();
    if(!b_input()||!gpio_get(A)||gpio_is_pulled_up(A)||gpio_is_dir_out(A)){
        printf("STREAM_ERROR input_or_direction_guard\n");ready();return;
    }
    printf("STREAM_BEGIN {\"version\":\"" VERSION "\",\"run\":%u,\"blocks\":%u,\"bytes_per_block\":16384,"
           "\"samples_per_block\":65536,\"sample_hz_nominal\":1000000,\"sample_hz_estimated\":null,"
           "\"sys_hz\":%u,\"clkdiv\":125,\"instructions_per_sample\":1,\"input_base\":12,"
           "\"A_drive\":\"low_or_high_z_start_only\",\"B_input_only\":true,\"hold_ms\":%u}\n",(uint)id,(uint)blocks,(uint)clock_get_hz(clk_sys),(uint)hold_ms);
    fflush(stdout);
    ownership[0]=BUSY;active=true;dma_set_irq0_channel_mask_enabled(dma_mask,true);
    dma_start_channel_mask(1u<<channels[0]);pio_sm_set_enabled(io,0,true);
    absolute_time_t deadline=make_timeout_time_ms((uint64_t)blocks*BLOCK_SAMPLES/1000u+1000u);
    sleep_us(2000);gpio_put(A,0);gpio_set_dir(A,GPIO_OUT);sleep_us(32);release_a();
    bool held=false;
    while(exported<produced||active){
        int command=getchar_timeout_us(0);
        if(command=='Q'){errors|=ERR_ABORT;halt_capture();}
        if(time_reached(deadline)){errors|=ERR_TIMEOUT;halt_capture();}
        if(exported==produced){tight_loop_contents();continue;}
        if(hold_ms&&!held){held=true;sleep_ms(hold_ms);}
        unsigned slot=exported%CHANNELS;
        uint32_t key=save_and_disable_interrupts();
        if(ownership[slot]!=READY||block_seq[slot]!=exported){errors|=ERR_ORDER;restore_interrupts(key);halt_capture();break;}
        ownership[slot]=SENDING;restore_interrupts(key);
        uint64_t start=(uint64_t)exported*BLOCK_SAMPLES;
        uint32_t header[11]={0x52545350u,0x3130304du,id,exported,(uint32_t)start,(uint32_t)(start>>32),BLOCK_BYTES,io->fdebug,errors,crc32(buffers[slot],BLOCK_BYTES),0};
        header[10]=crc32(header,40);
        fwrite(header,1,sizeof(header),stdout);fwrite(buffers[slot],1,BLOCK_BYTES,stdout);fflush(stdout);
        key=save_and_disable_interrupts();ownership[slot]=ARMED;++exported;restore_interrupts(key);
    }
    halt_capture();release_a();
    printf("\nSTREAM_END {\"run\":%u,\"requested_blocks\":%u,\"produced\":%u,\"exported\":%u,"
           "\"errors\":%u,\"fdebug\":%u,\"discarded_partial_words\":%u,\"A_released\":%s,\"B_input_only\":%s}\n",
           (uint)id,(uint)blocks,(uint)produced,(uint)exported,(uint)errors,(uint)io->fdebug,(uint)partial_discarded,
           gpio_is_dir_out(A)?"false":"true",b_input()?"true":"false");
    last_run=id;ready();
}
int main(void)
{
    set_sys_clock_khz(125000,true);pio_set_sm_mask_enabled(io,15,false);
    for(unsigned i=0;i<4;++i)pio_sm_set_consecutive_pindirs(io,i,12,4,false);
    for(uint pin=12;pin<=15;++pin){gpio_init(pin);gpio_set_dir(pin,GPIO_IN);gpio_put(pin,0);gpio_set_input_enabled(pin,true);gpio_disable_pulls(pin);}
    // GPIO12 stays SIO for low/high-Z start. PIO can observe its pad independently.
    pio_gpio_init(io,B);pio_sm_claim(io,0);offset=pio_add_program(io,&continuous_pair_program);
    for(unsigned i=0;i<CHANNELS;++i){channels[i]=dma_claim_unused_channel(true);dma_mask|=1u<<channels[i];}
    irq_set_exclusive_handler(DMA_IRQ_0,dma_irq);irq_set_enabled(DMA_IRQ_0,true);
    stdio_init_all();ready();char line[80];unsigned used=0;bool too_long=false;
    for(;;){
        int ch=getchar_timeout_us(1000);if(ch==PICO_ERROR_TIMEOUT)continue;
        if(ch=='\r'||ch=='\n'){
            line[used]=0;
            if(too_long)printf("STREAM_ERROR command_too_long\n");
            else if(!strcmp(line,"I"))ready();
            else if(used){unsigned id,blocks,hold;char extra;
                if(sscanf(line,"R %u %u %u %c",&id,&blocks,&hold,&extra)==3&&id&&id!=last_run&&blocks>=4&&blocks<=1850&&hold<=1000)
                    run(id,blocks,hold);
                else printf("STREAM_ERROR invalid_or_repeated_run\n");
            }
            used=0;too_long=false;fflush(stdout);
        }else if(used<sizeof(line)-1)line[used++]=(char)ch;else too_long=true;
    }
}
