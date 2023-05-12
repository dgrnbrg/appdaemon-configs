#include "esphome/core/log.h"
#include "drv2605.h"

namespace esphome {
namespace drv2605 {

static const char *TAG = "drv2605.component";

void DRV2605Component::setup() {
    // TODO wait 250uS
    // TODO pull EN pin high
    // TODO wait 250uS
    //
    uint8_t status;
    this->read_byte(STATUS_REG, &status);
    if (status != 0xE0) {
        ESP_LOGW(TAG, "status register %X in error");
        this->mark_failed();
        return;
    }

    this->write_byte(MODE_REG, 0x0); // Wake up from standby

    this->write_byte(MODE_REG, 0x7); // Move from standby to autocalibration
    uint8_t feedback_reg;
    this->read_byte(FEEDBACK_REG, &feedback_reg);
    // populate ERM_LRA
    feedback_reg |= 0x80; // enable LRA mode
    // populate FB_BRAKE_FACTOR - "2 for most actuators"
    feedback_reg &= ~0x70;
    feedback_reg |= 0x2 << 4;
    // populate LOOP_GAIN - "2 for most actuators"
    feedback_reg &= ~0x0C;
    feedback_reg |= 0x2 << 2;
    this->write_byte(FEEDBACK_REG, feedback_reg);

    // populate RATED_VOLTAGE
    this->write_byte(RATEDVOLT_REG, this->rated_voltage_reg_value);

    // populate OD_CLAMP
    this->write_byte(OVERDRIVECLAMP_REG, this->overdrive_reg_value);

    // popluate control4 register
    uint8_t control4_reg;
    this->read_byte(CONTROL4_REG, &control4_reg);
    // populate AUTO_CAL_TIME - "3 for most actuators"
    control4_reg |= 0x3 << 4;
    // populate ZC_DET_TIME - "0 for most actuators"
    control4_reg &= ~0x80;
    this->write_byte(CONTROL4_REG, control4_reg);

    // popluate control1 register
    uint8_t control1_reg;
    this->read_byte(CONTROL1_REG, &control1_reg);
    // populate DRIVE_TIME
    control1_reg &= ~0x1F;
    control1_reg |= this->drive_time_reg_value & 0x1F;
    this->write_byte(CONTROL1_REG, control1_reg);

    // populate control2 register
    uint8_t control2_reg;
    this->read_byte(CONTROL2_REG, &control2_reg);
    // populate SAMPLE_TIME - "3 for most actuators"
    control2_reg |= 0x30;
    // populate BLANKING_TIME - "1 for most actuators"
    control2_reg &= 0x0C;
    control2_reg |= 0x1 << 2;
    // populate IDISS_TIME - "1 for most actuators"
    control2_reg &= 0x03;
    control2_reg |= 0x1;
    this->write_byte(CONTROL2_REG, control2_reg);

    // Start autocalibration
    this->write_byte(GO_REG, 0x01);

    // Check that DIAG_RESULT in register 0x0 says autocalibration completed successfully
    this->read_byte(STATUS_REG, &status);
    if (status != 0xE0) {
        ESP_LOGW(TAG, "status register %X in error, calibration failed");
        this->mark_failed();
        return;
    }

    this->write_byte(MODE_REG, 0x0); // Move from autocalibration to internal trigger

    this->write_byte(LIB_REG, 6); // Select the tuned LRA library

    // TODO pull EN pin low
}

void DRV2605Component::fire_waveform(uint8_t waveform_id) {
    // Here's how to fire a waveform
    //TODO pull EN pin high
    this->write_byte(MODE_REG, 0x0); // Wake up from standby to internal trigger
    this->write_byte(WAVESEQ1, waveform_id);
    this->write_byte(GO_REG, 0x01);
    // TODO pull EN pin low
}

void DRV2605Component::loop() {

}

void DRV2605Component::dump_config(){
    ESP_LOGCONFIG(TAG, "DRV2605");
    ESP_LOGCONFIG(TAG, "  Overdrive reg = %d", this->overdrive_reg_value);
    ESP_LOGCONFIG(TAG, "  Rated voltage reg = %d", this->rated_voltage_reg_value);
    LOG_PIN("  EN pin:", this->en_pin_);
}


}  // namespace drv2605
}  // namespace esphome
