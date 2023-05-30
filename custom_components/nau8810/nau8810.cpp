
#include "esphome/core/log.h"
#include "nau8810.h"

extern "C" {

static const char *TAG = "nau8810.c_bridge";

void nau8810_I2C_Write(void * p, uint8_t i2c_address, uint8_t reg, uint16_t value)
{
    esphome::nau8810::NAU8810Component * c = (esphome::nau8810::NAU8810Component *)p;
    uint8_t reg_, value_;
    reg_ = reg * 2;
    reg_ |= (value >> 8) & 0x1;
    value_ = (uint8_t) value;
    ESP_LOGD(TAG, "Writing byte via %p to reg 0x%x with value 0x%x (addr=0x%x data=0x%x)", c, reg, value, reg_, value_);
    if (!c->write_byte(reg_, value_)) {
        c->status_set_warning();
    }
}

uint16_t nau8810_I2C_Read(void * p, uint8_t i2c_address, uint8_t reg)
{
    esphome::nau8810::NAU8810Component * c = (esphome::nau8810::NAU8810Component *)p;
    reg *= 2;
    uint16_t result;
    if (!c->read_byte_16(reg, &result)) {
        c->status_set_warning();
    }
    return result;
}

}

namespace esphome {
namespace nau8810 {

static const char *TAG = "nau8810.component";

void NAU8810Component::setup() {
    bool done = false;
    uint16_t b;
    nau8810.comm_handle = (void*)this;
    NAU881x_Init(&nau8810);
    NAU881x_Get_SiliconRevision(&nau8810, &this->silicon_revision_);
    // Make audio codec functional
    // Enable micbias (should default to 0.9*va)
    NAU881x_Set_MicBias_Enable(&nau8810, 1);
    // Code below will route the audio from MICN to Speaker via Bypass (refer to General Block Diagram)
    NAU881x_Set_PGA_Input(&nau8810, NAU881X_INPUT_MICN);
    NAU881x_Set_Output_Enable(&nau8810, NAU881X_OUTPUT_SPK);

    // David's customizations
    NAU881x_Set_Speaker_Source(&nau8810, NAU881X_OUTPUT_FROM_DAC);
    // Enable DAC
    NAU881x_Set_DAC_Enable(&nau8810, 1);
    // Enable automute for DAC output when idle
    NAU881x_Set_DAC_AutoMute(&nau8810, 1);
    // Enable PGA
    NAU881x_Set_PGA_Enable(&nau8810, 1);
    // Enable autoleveling
    //NAU881x_Set_ALC_Enable(&nau8810, 1);
    NAU881x_Set_PGA_Gain(&nau8810, 0x3f);
    // Enable the ADC
    NAU881x_Set_ADC_Enable(&nau8810, 1);
    // Send mic data to left & right channels
    NAU881x_Set_LOUTR(&nau8810, 1);
    // Use I2S for audio data
    NAU881x_Set_AudioInterfaceFormat(&nau8810, NAU881X_AUDIO_IFACE_FMT_I2S, NAU881X_AUDIO_IFACE_WL_16BITS);
    // Configure slave clocking
    NAU881x_Set_Clock(&nau8810, 0, NAU881X_BCLKDIV_1, NAU881X_MCLKDIV_1, NAU881X_CLKSEL_MCLK);
#if 0
    for (int i = 0; i < 80; i++) {
        this->read_byte_16(i*2, &b);
        ESP_LOGD(TAG, "[3rd] NAU8810 regsiter 0x%x(%d) has value 0x%x", i, i, b);
    }
#endif
}

void NAU8810Component::set_speaker_mute(bool state) {
    if (!this->is_ready()) {
        return;
    }
    NAU881x_Set_Speaker_Mute(&nau8810, state);
}

uint8_t NAU8810Component::get_speaker_volume() {
    return NAU881x_Get_Speaker_Volume(&nau8810);
}

void NAU8810Component::set_speaker_volume(uint8_t volume) {
    if (!this->is_ready()) {
        return;
    }
    NAU881x_Set_Speaker_Volume(&nau8810, volume);
}

void NAU8810Component::loop() {
    // TODO add mic & speaker media controls (volume + mute)
}

void NAU8810Component::dump_config(){
    ESP_LOGCONFIG(TAG, "NAU8810, silicon rev 0x%x", this->silicon_revision_);
}


}  // namespace nau8810
}  // namespace esphome
