#pragma once

#include "esphome/core/defines.h"
#include "esphome/core/component.h"
#include "esphome/core/automation.h"
#include "esphome/core/helpers.h"
#include "esphome/core/preferences.h"
#include "esphome/components/binary_sensor/binary_sensor.h"


namespace esphome {
namespace presence_combo {

class PresenceComboComponent : public esphome::Component, public esphome::binary_sensor::BinarySensor
{
  public:
   PresenceComboComponent() {}

    void dump_config() override;
    void loop() override;
    void setup() override;
  
    float get_setup_priority() const;
  
    void add_child_sensor(binary_sensor::BinarySensor * child) {
        children_.push_back(child);
    }
  
  protected:
    std::vector<binary_sensor::BinarySensor*> children_;
    bool state_;
  
};
}
}
