#include "esphome/core/log.h"
#include "drv2605.h"

namespace esphome {
namespace drv2605 {

static const char *TAG = "drv2605.component";

void DRV2605Component::setup() {
    this->pref_ = global_preferences->make_preference<DRV2605CalibrationData>(this->name_hash_);
    if (!this->pref_.load(&this->calibration_data_)) {
        ESP_LOGW(TAG, "Calibration data not found. Please run calibration before proceeding");
        this->has_calibration = false;
    } else {
        this->has_calibration = true;
        ESP_LOGW(TAG, "Calibration data found.");
    }

    this->en_pin_->setup();
    this->en_pin_->digital_write(0);
    delay(25);
    this->pending_reset_ = false;
    this->en_pending_deassert_ = false;
    this->pending_calibrate_ = false;
    this->reset();
}

void DRV2605Component::reset() {
    this->en_pin_->digital_write(1);
    delay(25);
    this->write_byte(MODE_REG, 0x80); // Perform a reset
    this->pending_reset_ = true;
    ESP_LOGD(TAG, "Initiated reset");
}

void DRV2605Component::populate_config_regs() {
    uint8_t feedback_reg;
    this->read_byte(FEEDBACK_REG, &feedback_reg);
    // populate ERM_LRA
    feedback_reg |= 0x80; // enable LRA mode
    // populate FB_BRAKE_FACTOR - "2 for most actuators"
    feedback_reg &= ~0x70;
    feedback_reg |= 0x3 << 4; // deviate to 3
    // populate LOOP_GAIN - "2 for most actuators"
    feedback_reg &= ~0x0C;
    feedback_reg |= 0x1 << 2; // deviate to 1
    if (this->has_calibration) {
        // include bemf_gain
        feedback_reg &= ~0x3;
        feedback_reg |= this->calibration_data_.bemf_gain & 0x3;
    }
    ESP_LOGD(TAG, "Feedback reg (0x%x) = 0x%x", FEEDBACK_REG, feedback_reg);
    this->write_byte(FEEDBACK_REG, feedback_reg);

    // populate RATED_VOLTAGE
    this->write_byte(RATEDVOLT_REG, this->rated_voltage_reg_value);

    // populate OD_CLAMP
    this->write_byte(OVERDRIVECLAMP_REG, this->overdrive_reg_value);

    // popluate control1 register
    uint8_t control1_reg;
    this->read_byte(CONTROL1_REG, &control1_reg);
    // populate DRIVE_TIME
    control1_reg &= ~0x1F;
    control1_reg |= this->drive_time_reg_value & 0x1F;
    ESP_LOGD(TAG, "Control1 reg (0x%x) = 0x%x", CONTROL1_REG, control1_reg);
    this->write_byte(CONTROL1_REG, control1_reg);

    // populate control2 register
    uint8_t control2_reg;
    this->read_byte(CONTROL2_REG, &control2_reg);
    // populate SAMPLE_TIME - "3 for most actuators"
    control2_reg |= 0x30;
    // populate BLANKING_TIME - "1 for most actuators"
    control2_reg &= ~0x0C;
    control2_reg |= 0x1 << 2;
    // populate IDISS_TIME - "1 for most actuators"
    control2_reg &= ~0x03;
    control2_reg |= 0x1;
    ESP_LOGD(TAG, "Control2 reg (0x%x) = 0x%x", CONTROL2_REG, control2_reg);
    this->write_byte(CONTROL2_REG, control2_reg);

    // populate control3 register
    uint8_t control3_reg;
    this->read_byte(CONTROL3_REG, &control3_reg);
    // Turn off ERM open loop mode
    control3_reg &= ~0x20;
    ESP_LOGD(TAG, "Control3 reg (0x%x) = 0x%x", CONTROL3_REG, control3_reg);
    this->write_byte(CONTROL3_REG, control3_reg);

    // popluate control4 register
    uint8_t control4_reg;
    this->read_byte(CONTROL4_REG, &control4_reg);
    // populate AUTO_CAL_TIME - "3 for most actuators"
    control4_reg |= 0x3 << 4;
    // populate ZC_DET_TIME - "0 for most actuators"
    control4_reg &= ~0x80;
    ESP_LOGD(TAG, "Control4 reg (0x%x) = 0x%x", CONTROL4_REG, control4_reg);
    this->write_byte(CONTROL4_REG, control4_reg);

    if (this->has_calibration) {
        ESP_LOGD(TAG, "Including calibration regs");
        // include other calibration regs
        this->write_byte(COMPRESULT_REG, this->calibration_data_.compensation);
        this->write_byte(BACKEMF_REG, this->calibration_data_.backemf);
    }
}

void DRV2605Component::calibrate() {
    this->en_pin_->digital_write(1);
    this->write_byte(MODE_REG, 0x0); // Move to out of standby
    delay(25);
    this->write_byte(MODE_REG, 0x7); // Move from standby to autocalibration

    this->has_calibration = false; // ensure we recalibrate
    this->populate_config_regs();

    // Start autocalibration
    this->write_byte(GO_REG, 0x01);
    this->pending_calibrate_ = true;
    ESP_LOGD(TAG, "Started calibration");
}

void DRV2605Component::fire_waveform(uint8_t waveform_id) {
    // Here's how to fire a waveform
    ESP_LOGD(TAG, "Firing a waveform %d", waveform_id);
    // pull EN pin high
    this->en_pin_->digital_write(1);
    delay(25);
    this->write_byte(MODE_REG, 0x0); // Wake up from standby to internal trigger
    delay(25);
    this->write_byte(WAVESEQ1, waveform_id);
    delay(25);
    this->write_byte(GO_REG, 0x01);
    // We'll deassert the enable pin in the loop
    this->en_pending_deassert_ = true;
}

void DRV2605Component::loop() {
    if (this->pending_reset_) {
        uint8_t status;
        this->read_byte(MODE_REG, &status);
        if (status & 0x80) {
            // reset still in progress
            ESP_LOGD(TAG, "waiting for reset, mode is %x", status);
        } else {
            this->read_byte(STATUS_REG, &status);
            if (status != 0xE0) {
                ESP_LOGW(TAG, "status register %X in error", status);
                this->mark_failed();
                return;
            }
            ESP_LOGI(TAG, "drv2605 reset completed");
            this->write_byte(MODE_REG, 0x0); // Wake up from standby

            if (this->has_calibration) {
                ESP_LOGD(TAG, "populating config after reset");
                this->populate_config_regs();
            } else {
                ESP_LOGD(TAG, "don't forget to run autocalibration");
            }
            // pull EN pin low
            this->en_pin_->digital_write(0);
            this->pending_reset_ = false;
        }
    }
    if (this->en_pending_deassert_) {
        uint8_t go_bit;
        this->read_byte(GO_REG, &go_bit);
        if (!go_bit) {
            this->write_byte(MODE_REG, 0x0); // Move from autocalibration to internal trigger
            // pull EN pin low
            this->en_pin_->digital_write(0);
            this->en_pending_deassert_ = false;
        }
    }
    if (this->pending_calibrate_) {
        uint8_t status;
        this->read_byte(GO_REG, &status);
        if (!status) {
            ESP_LOGD(TAG, "Autocalibration complete");

            // Check that DIAG_RESULT in register 0x0 says autocalibration completed successfully
            this->read_byte(STATUS_REG, &status);
            if (status != 0xE0) {
                ESP_LOGW(TAG, "status register %X in error, calibration failed", status);
                this->mark_failed();
                return;
            }

            this->read_byte(FEEDBACK_REG, &this->calibration_data_.bemf_gain);
            this->calibration_data_.bemf_gain &= 0x3;
            ESP_LOGI(TAG, "BEMF gain = %d", this->calibration_data_.bemf_gain);
            this->read_byte(COMPRESULT_REG, &this->calibration_data_.compensation);
            ESP_LOGI(TAG, "Autocalibration compensation = %d", this->calibration_data_.compensation);
            this->read_byte(BACKEMF_REG, &this->calibration_data_.backemf);
            ESP_LOGI(TAG, "Autocalibration back emf = %d", this->calibration_data_.backemf);
            this->pref_.save(&this->calibration_data_);
            ESP_LOGI(TAG, "Saved autocalibration data");

            this->write_byte(MODE_REG, 0x0); // Move from autocalibration to internal trigger

            this->write_byte(LIB_REG, 6); // Select the tuned LRA library

            // pull EN pin low
            this->en_pin_->digital_write(0);
            this->pending_calibrate_ = false;
        } else {
            ESP_LOGD(TAG, "Still waiting for calibration to complete");
        }
    }
    //uint8_t status;
    //this->read_byte(STATUS_REG, &status);
    //ESP_LOGW(TAG, "status register %X", status);
}

void DRV2605Component::dump_config(){
    ESP_LOGCONFIG(TAG, "DRV2605");
    ESP_LOGCONFIG(TAG, "  Overdrive reg = %d", this->overdrive_reg_value);
    ESP_LOGCONFIG(TAG, "  Rated voltage reg = %d", this->rated_voltage_reg_value);
    LOG_PIN("  EN pin:", this->en_pin_);
    if (!this->has_calibration) {
        ESP_LOGCONFIG(TAG, "  No calibration data found");
    } else {
        ESP_LOGCONFIG(TAG, "  Calibration data:");
        ESP_LOGCONFIG(TAG, "    bemf gain = 0x%x", this->calibration_data_.bemf_gain);
        ESP_LOGCONFIG(TAG, "    compensation = %d", this->calibration_data_.compensation);
        ESP_LOGCONFIG(TAG, "    backemf = %d", this->calibration_data_.backemf);
    }
}


}  // namespace drv2605
}  // namespace esphome
