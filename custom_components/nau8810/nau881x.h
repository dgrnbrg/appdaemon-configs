#ifndef MR_NAU881X_H
#define MR_NAU881X_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include "nau881x_regs.h"

extern void nau8810_I2C_Write(void * p, uint8_t i2c_address, uint8_t reg, uint16_t value);
extern uint16_t nau8810_I2C_Read(void * p, uint8_t i2c_address, uint8_t reg);

#define NAU881X_REG_WRITE(handle, reg, val) nau8810_I2C_Write(handle, NAU881X_I2C_ADDRESS, reg, val)
#define NAU881X_REG_READ(handle, reg) nau8810_I2C_Read(handle, NAU881X_I2C_ADDRESS, reg)

// Volume range: -57 - +6 dB
#define NAU881X_SPKVOL_DB_TO_REG_VALUE(vol_db) ((vol_db + 57) & 0x3F)
#define NAU881X_SPKVOL_REG_VALUE_TO_DB(vol_regval) (vol_regval - 57)


typedef struct _NAU881x
{
    void * comm_handle;
    uint16_t _register[80];
} NAU881x_t;


typedef enum _nau881x_status
{
    NAU881X_STATUS_OK = 0,
    NAU881X_STATUS_ERROR = 1,
    NAU881X_STATUS_INVALID = 2
} nau881x_status_t;


nau881x_status_t NAU881x_Init(NAU881x_t* nau881x);

// Input path
nau881x_status_t NAU881x_Get_PGA_Input(NAU881x_t* nau881x, nau881x_input_t* input);
nau881x_status_t NAU881x_Set_PGA_Input(NAU881x_t* nau881x, nau881x_input_t input);
uint8_t NAU881x_Get_PGA_Gain(NAU881x_t* nau881x);
nau881x_status_t NAU881x_Set_PGA_Gain(NAU881x_t* nau881x, uint8_t vol);
nau881x_status_t NAU881x_Set_PGA_Gain_db(NAU881x_t* nau881x, float vol_db);
nau881x_status_t NAU881x_Set_PGA_Mute(NAU881x_t* nau881x, uint8_t state);
nau881x_status_t NAU881x_Set_PGA_ZeroCross(NAU881x_t* nau881x, uint8_t state);
nau881x_status_t NAU881x_Set_PGA_Enable(NAU881x_t* nau881x, uint8_t enable);
nau881x_status_t NAU8814_Set_Aux_Enable(NAU881x_t* nau8814, uint8_t enable);
nau881x_status_t NAU8814_Set_Aux_Mode(NAU881x_t* nau8814, nau881x_aux_mode_t mode);
nau881x_status_t NAU881x_Set_PGA_Boost(NAU881x_t* nau881x, uint8_t state);
nau881x_status_t NAU881x_Set_Boost_Volume(NAU881x_t* nau881x, nau881x_input_t input, uint8_t vol);
nau881x_status_t NAU881x_Set_Boost_Enable(NAU881x_t* nau881x, uint8_t enable);
nau881x_status_t NAU881x_Set_MicBias_Enable(NAU881x_t* nau881x, uint8_t enable);
nau881x_status_t NAU881x_Set_MicBias_Voltage(NAU881x_t* nau881x, uint8_t val);
nau881x_status_t NAU881x_Set_MicBiasMode_Enable(NAU881x_t* nau881x, uint8_t enable);

// ADC digital filter
nau881x_status_t NAU881x_Set_ADC_Enable(NAU881x_t* nau881x, uint8_t enable);
nau881x_status_t NAU881x_Set_ADC_Polarity(NAU881x_t* nau881x, uint8_t invert);
nau881x_status_t NAU881x_Set_ADC_OverSampleRate(NAU881x_t* nau881x, nau881x_adc_oversamplerate_t rate);
nau881x_status_t NAU881x_Set_ADC_HighPassFilter(NAU881x_t* nau881x, uint8_t enable, nau881x_hpf_mode_t mode, uint8_t freq_regval);
nau881x_status_t NAU881x_Set_ADC_Gain(NAU881x_t* nau881x, uint8_t regval);

// ALC
nau881x_status_t NAU881x_Set_ALC_Enable(NAU881x_t* nau881x, uint8_t enable);
nau881x_status_t NAU881x_Set_ALC_Gain(NAU881x_t* nau881x, uint8_t minval, uint8_t maxval);
nau881x_status_t NAU881x_Set_ALC_TargetLevel(NAU881x_t* nau881x, uint8_t val);
nau881x_status_t NAU881x_Set_ALC_Hold(NAU881x_t* nau881x, uint8_t val);
nau881x_status_t NAU881x_Set_ALC_Mode(NAU881x_t* nau881x, nau881x_alc_mode_t mode);
nau881x_status_t NAU881x_Set_ALC_AttackTime(NAU881x_t* nau881x, uint8_t val);
nau881x_status_t NAU881x_Set_ALC_DecayTime(NAU881x_t* nau881x, uint8_t val);
nau881x_status_t NAU881x_Set_ALC_ZeroCross(NAU881x_t* nau881x, uint8_t state);
nau881x_status_t NAU881x_Set_ALC_NoiseGate_Threshold(NAU881x_t* nau881x, uint8_t val);
nau881x_status_t NAU881x_Set_ALC_NoiseGate_Enable(NAU881x_t* nau881x, uint8_t enable);

// DAC digital filter
nau881x_status_t NAU881x_Set_ADC_DAC_Passthrough(NAU881x_t* nau881x, uint8_t enable);
nau881x_status_t NAU881x_Set_DAC_Enable(NAU881x_t* nau881x, uint8_t enable);
nau881x_status_t NAU881x_Set_DAC_Polarity(NAU881x_t* nau881x, uint8_t invert);
nau881x_status_t NAU881x_Set_DAC_Gain(NAU881x_t* nau8810, uint8_t val);
nau881x_status_t NAU881x_Set_DAC_SoftMute(NAU881x_t* nau881x, uint8_t state);
nau881x_status_t NAU881x_Set_DAC_AutoMute(NAU881x_t* nau881x, uint8_t state);
nau881x_status_t NAU881x_Set_DAC_SampleRate(NAU881x_t* nau881x, nau881x_dac_samplerate_t rate);
nau881x_status_t NAU881x_Set_DAC_Limiter_Enable(NAU881x_t* nau881x, uint8_t enable);
nau881x_status_t NAU881x_Set_DAC_Limiter_AttackTime(NAU881x_t* nau881x, uint8_t val);
nau881x_status_t NAU881x_Set_DAC_Limiter_DecayTime(NAU881x_t* nau881x, uint8_t val);
nau881x_status_t NAU881x_Set_DAC_Limiter_VolumeBoost(NAU881x_t* nau881x, uint8_t value);
nau881x_status_t NAU881x_Set_DAC_Limiter_Threshold(NAU881x_t* nau881x, int8_t value);
nau881x_status_t NAU881x_Set_Equalizer_Path(NAU881x_t* nau881x, nau881x_eq_path_t path);
nau881x_status_t NAU881x_Set_Equalizer_Bandwidth(NAU881x_t* nau881x, uint8_t equalizer_no, nau881x_eq_bandwidth_t bandwidth);
nau881x_status_t NAU881x_Set_Equalizer_Gain(NAU881x_t* nau881x, uint8_t equalizer_no, int8_t value);
nau881x_status_t NAU881x_Set_Equalizer1_Frequency(NAU881x_t* nau881x, nau881x_eq1_cutoff_freq_t cutoff_freq);
nau881x_status_t NAU881x_Set_Equalizer2_Frequency(NAU881x_t* nau881x, nau881x_eq2_center_freq_t center_freq);
nau881x_status_t NAU881x_Set_Equalizer3_Frequency(NAU881x_t* nau881x, nau881x_eq3_center_freq_t center_freq);
nau881x_status_t NAU881x_Set_Equalizer4_Frequency(NAU881x_t* nau881x, nau881x_eq4_center_freq_t center_freq);
nau881x_status_t NAU881x_Set_Equalizer5_Frequency(NAU881x_t* nau881x, nau881x_eq5_cutoff_freq_t cutoff_freq);

// Analog outputs
nau881x_status_t NAU881x_Set_Output_Enable(NAU881x_t* nau881x, nau881x_output_t output);
nau881x_status_t NAU881x_Set_Speaker_Source(NAU881x_t* nau881x, nau881x_output_source_t source);
nau881x_status_t NAU881x_Set_Speaker_FromBypass_Attenuation(NAU881x_t* nau881x, uint8_t enable);
nau881x_status_t NAU881x_Set_Speaker_Boost(NAU881x_t* nau881x, uint8_t enable);
nau881x_status_t NAU881x_Set_Speaker_ZeroCross(NAU881x_t* nau881x, uint8_t state);
nau881x_status_t NAU881x_Set_Speaker_Mute(NAU881x_t* nau881x, uint8_t state);
nau881x_status_t NAU881x_Set_Speaker_Volume(NAU881x_t* nau881x, uint8_t val);
nau881x_status_t NAU881x_Set_Speaker_Volume_db(NAU881x_t* nau881x, int8_t vol_db);
uint8_t NAU881x_Get_Speaker_Volume(NAU881x_t* nau881x);
uint8_t NAU881x_Get_Speaker_Volume_db(NAU881x_t* nau881x);
nau881x_status_t NAU881x_Set_Mono_Source(NAU881x_t* nau881x, nau881x_output_source_t source);
nau881x_status_t NAU881x_Set_Mono_FromBypass_Attenuation(NAU881x_t* nau881x, uint8_t enable);
nau881x_status_t NAU881x_Set_Mono_Boost(NAU881x_t* nau881x, uint8_t enable);
nau881x_status_t NAU881x_Set_Mono_Mute(NAU881x_t* nau881x, uint8_t state);

// General purpose control
nau881x_status_t NAU881x_Set_SlowClock_Enable(NAU881x_t* nau881x, uint8_t enable);
nau881x_status_t NAU8814_Set_GPIO_Control(NAU881x_t* nau8814, nau8814_gpio_function_t function, uint8_t invert_polarity);
nau881x_status_t NAU8814_Set_ThermalShutdown_Enable(NAU881x_t* nau8814, uint8_t enable);

// Clock generation
nau881x_status_t NAU881x_Set_PLL_Enable(NAU881x_t* nau881x, uint8_t enable);
nau881x_status_t NAU881x_Set_PLL_FrequencyRatio(NAU881x_t* nau881x, uint8_t mclk_div2, uint8_t N, uint32_t K);

// Control interface
nau881x_status_t NAU8814_Set_ControlInterface_SPI24bit(NAU881x_t* nau8814, uint8_t enable);

// Digital audio interface
nau881x_status_t NAU881x_Set_AudioInterfaceFormat(NAU881x_t* nau881x, nau881x_audio_iface_fmt_t format, nau881x_audio_iface_wl_t word_length);
nau881x_status_t NAU881x_Set_PCM_Timeslot(NAU881x_t* nau881x, uint16_t timeslot);
nau881x_status_t NAU881x_Set_FrameClock_Polarity(NAU881x_t* nau881x, uint8_t invert);
nau881x_status_t NAU881x_Set_BCLK_Polarity(NAU881x_t* nau881x, uint8_t invert);
nau881x_status_t NAU881x_Set_ADC_Data_Phase(NAU881x_t* nau8814, uint8_t in_right_phase_of_frame);
nau881x_status_t NAU881x_Set_DAC_Data_Phase(NAU881x_t* nau8814, uint8_t in_right_phase_of_frame);
nau881x_status_t NAU881x_Set_Clock(NAU881x_t* nau881x, uint8_t is_master, nau881x_bclkdiv_t bclk_divider, nau881x_mclkdiv_t mclk_divider, nau881x_clksel_t clock_source);
nau881x_status_t NAU881x_Set_LOUTR(NAU881x_t* nau881x, uint8_t enable);
nau881x_status_t NAU881x_Set_ADC_Companding(NAU881x_t* nau881x, nau881x_companding_t companding);
nau881x_status_t NAU881x_Set_DAC_Companding(NAU881x_t* nau881x, nau881x_companding_t companding);
nau881x_status_t NAU881x_Set_Companding_WordLength_8bit(NAU881x_t* nau881x, uint8_t enable);

// Other
nau881x_status_t NAU881x_Get_SiliconRevision(NAU881x_t* nau881x, uint8_t* silicon_revision);

#ifdef __cplusplus
}
#endif

#endif // MR_NAU881X_H
