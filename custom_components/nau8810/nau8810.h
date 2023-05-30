#pragma once

#include "esphome/core/component.h"
#include "esphome/core/hal.h"
#include "esphome/core/defines.h"
#include "esphome/core/preferences.h"
#include "esphome/core/automation.h"
#include "esphome/components/i2c/i2c.h"
#include "nau881x.h"

namespace esphome {
namespace nau8810 {

class NAU8810Component : public i2c::I2CDevice, public Component {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;
  void set_speaker_volume(uint8_t);
  uint8_t get_speaker_volume();
  void set_speaker_mute(bool muted);
 protected:
    NAU881x_t nau8810;
    uint8_t silicon_revision_;
};

template<typename... Ts> class SetSpeakerVolumeAction : public Action<Ts...> {
 public:
  SetSpeakerVolumeAction(NAU8810Component *parent) : parent_(parent) {}
  TEMPLATABLE_VALUE(uint8_t, volume);

  void play(Ts... x) override {
    uint8_t volume = this->volume_.value(x...);
    this->parent_->set_speaker_volume(volume);
  }

  NAU8810Component *parent_;
};

}  // namespace nau8810
}  // namespace esphome

