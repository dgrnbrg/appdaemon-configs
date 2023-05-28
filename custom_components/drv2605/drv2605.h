#pragma once

#include "esphome/core/component.h"
#include "esphome/core/hal.h"
#include "esphome/core/defines.h"
#include "esphome/core/preferences.h"
#include "esphome/core/automation.h"
#include "esphome/components/i2c/i2c.h"

//The Status Register (0x00): The Device ID is bits 7-5. For DRV2605L it should be 7 or 111. 
//bits 4 and 2 are reserved. Bit 3 is the diagnostic result. You want to see 0. 
//bit 1 is the over temp flag, you want this to be 0
//bit 0 is  over current flag, you want this to be zero. 
// Ideally the register will read 0xE0.
#define STATUS_REG 0x00 

//The Mode Register (0x01): 
//Default 010000000 -- Need to get it out of Standby
//Set to 0000 0000=0x00 to use Internal Trigger
//Set to 0000 0001=0x01 to use External Trigger (edge mode)(like a switch on the IN pin)
//Set to 0000 0010=0x02 to use External Trigger (level mode)
//Set to 0000 0011=0x03 to use PWM input and analog output
//Set to 0000 0100=0x04 to use Audio to Vibe 
//Set to 0000 0101=0x05 to use Real-Time Playback
//Set to 0000 0110=0x06 to perform a diagnostic test - result stored in Diagnostic bit in register 0x00
//Set to 0000 0111 =0x07 to run auto calibration 
#define MODE_REG 0x01

//The Feedback Control Register (0x1A)
//bit 7: 0 for ERM, 1 for LRA -- Default is 0
//Bits 6-4 control brake factor
//bits 3-2 control the Loop gain
//bit 1-0 control the BEMF gain
#define FEEDBACK_REG 0x1A

//The Real-Time Playback Register (0x02)
//There are 6 ERM libraries. 
#define RTP_REG 0x02

//The Library Selection Register (0x03)
//See table 1 in Data Sheet for 
#define LIB_REG 0x03

//The waveform Sequencer Register (0X04 to 0x0B)
#define WAVESEQ1 0x04 //Bit 7: set this include a wait time between playback                                                                                                                                                                                 
#define WAVESEQ2 0x05
#define WAVESEQ3 0x06
#define WAVESEQ4 0x07
#define WAVESEQ5 0x08
#define WAVESEQ6 0x09
#define WAVESEQ7 0x0A
#define WAVESEQ8 0x0B

//The Go register (0x0C)
//Set to 0000 0001=0x01 to set the go bit
#define GO_REG 0x0C

//The Overdrive Time Offset Register (0x0D)
//Only useful in open loop mode
#define OVERDRIVE_REG 0x0D

//The Sustain Time Offset, Positive Register (0x0E)
#define SUSTAINOFFSETPOS_REG 0x0E

//The Sustain Time Offset, Negative Register (0x0F)
#define SUSTAINOFFSETNEG_REG 0x0F

//The Break Time Offset Register (0x10)
#define BREAKTIME_REG 0x10

//The Audio to Vibe control Register (0x11)
#define AUDIOCTRL_REG 0x11

//The Audio to vibe minimum input level Register (0x12)
#define AUDMINLVL_REG 0x12

//The Audio to Vibe maximum input level Register (0x13)
#define AUDMAXLVL_REG 0x13

// Audio to Vibe minimum output Drive Register (0x14)
#define AUDMINDRIVE_REG 0x14

//Audio to Vibe maximum output Drive Register (0x15)
#define AUDMAXDRIVE_REG 0X15

//The rated Voltage Register (0x16)
#define RATEDVOLT_REG 0x16

//The Overdive clamp Voltage (0x17)
#define OVERDRIVECLAMP_REG 0x17

//The Auto-Calibration Compensation - Result Register (0x18)
#define COMPRESULT_REG 0x18

//The Auto-Calibration Back-EMF Result Register (0x19)
#define BACKEMF_REG 0x19

//The Control1 Register (0x1B)
//For AC coupling analog inputs and 
//Controlling Drive time 
#define CONTROL1_REG 0x1B

//The Control2 Register (0x1C)
//See Data Sheet page 45
#define CONTROL2_REG 0x1C

//The COntrol3 Register (0x1D)
//See data sheet page 48
#define CONTROL3_REG 0x1D

//The Control4 Register (0x1E)
//See Data sheet page 49
#define CONTROL4_REG 0x1E

//The Control5 Register (0x1F)
//See Data Sheet page 50
#define CONTROL5_REG 0X1F

//The LRA Open Loop Period Register (0x20)
//This register sets the period to be used for driving an LRA when 
//Open Loop mode is selected: see data sheet page 50.
#define OLP_REG 0x20

//The V(Batt) Voltage Monitor Register (0x21)
//This bit provides a real-time reading of the supply voltage 
//at the VDD pin. The Device must be actively sending a waveform to take 
//reading Vdd=Vbatt[7:0]*5.6V/255
#define VBATMONITOR_REG 0x21

//The LRA Resonance-Period Register 
//This bit reports the measurement of the LRA resonance period
#define LRARESPERIOD_REG 0x22

namespace esphome {
namespace drv2605 {

struct DRV2605CalibrationData {
    uint8_t bemf_gain;
    uint8_t compensation;
    uint8_t backemf;
};

class DRV2605Component : public i2c::I2CDevice, public Component {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;
  void set_en_pin(GPIOPin *pin) { this->en_pin_ = pin; }
  void set_rated_voltage_reg(uint8_t x) { this->rated_voltage_reg_value = x; }
  void set_overdrive_reg(uint8_t x) { this->overdrive_reg_value = x; }
  void set_drive_time_reg_value(uint8_t x) { this->drive_time_reg_value = x; }
  void fire_waveform(uint8_t waveform_id);
  void calibrate();
  void reset();
  void set_name_hash(uint32_t name_hash) { this->name_hash_ = name_hash; }

 protected:
  void populate_config_regs();
    GPIOPin *en_pin_;
    bool en_pending_deassert_;
    bool pending_reset_;
    bool pending_calibrate_;
    uint8_t rated_voltage_reg_value;
    uint8_t overdrive_reg_value;
    uint8_t drive_time_reg_value;
    ESPPreferenceObject pref_;
    DRV2605CalibrationData calibration_data_;
    bool has_calibration;
    uint32_t name_hash_{};
};


template<typename... Ts> class FireHapticAction : public Action<Ts...> {
 public:
  FireHapticAction(DRV2605Component *parent) : parent_(parent) {}
  TEMPLATABLE_VALUE(uint8_t, waveform_id);

  void play(Ts... x) override {
    uint8_t waveform_id = this->waveform_id_.value(x...);
    this->parent_->fire_waveform(waveform_id);
  }

  DRV2605Component *parent_;
};

template<typename... Ts> class CalibrateAction : public Action<Ts...> {
 public:
  CalibrateAction(DRV2605Component *parent) : parent_(parent) {}

  void play(Ts... x) override {
    this->parent_->calibrate();
  }

  DRV2605Component *parent_;
};

template<typename... Ts> class ResetAction : public Action<Ts...> {
 public:
  ResetAction(DRV2605Component *parent) : parent_(parent) {}

  void play(Ts... x) override {
    this->parent_->reset();
  }

  DRV2605Component *parent_;
};

}  // namespace drv2605
}  // namespace esphome

