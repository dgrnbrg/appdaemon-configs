#include "presence_combo.h"
#include "esphome/core/log.h"

namespace esphome {
namespace presence_combo {

    static const char *const TAG = "presence_combo";

    void PresenceComboComponent::setup() {
        this->state_ = false;
        for (auto& c : children_) {
            if (c->state) {
                this->state_ = true;
            }
        }
        this->publish_initial_state(this->state_);
    }

    void PresenceComboComponent::dump_config() {
        LOG_BINARY_SENSOR("", "Presence Combo Sensor", this);
        for (auto& c : children_) {
            LOG_BINARY_SENSOR("  ", "Sub-sensor", c);
        }
    }

    float PresenceComboComponent::get_setup_priority() const {
        return setup_priority::DATA;
    }

    void PresenceComboComponent::loop() {
        this->state_ = false;
        for (auto& c : children_) {
            if (c->state) {
                this->state_ = true;
            }
        }
        this->publish_state(this->state_);
    }

}
}
