#pragma once

#include "esphome/core/defines.h"
#include "esphome/core/component.h"
#include "esphome/core/automation.h"
#include "esphome/core/helpers.h"
#include "esphome/core/preferences.h"
#include "esphome/components/text_sensor/text_sensor.h"
#include "esphome/components/esp32_ble/ble.h"
#include "esphome/components/esp32_ble/ble_advertising.h"
#include "esphome/components/esp32_ble/ble_uuid.h"
#include "esphome/components/esp32_ble/queue.h"
#include "esphome/components/esp32_ble_server/ble_service.h"
#include "esphome/components/esp32_ble_server/ble_server.h"
#include "esphome/components/esp32_ble_server/ble_characteristic.h"


#ifdef USE_ESP32

#include <esp_gap_ble_api.h>
#include <esp_gatts_api.h>


namespace esphome {
namespace irk_enrollment {

class IrkEnrollmentComponent :
	public esphome::Component, 
	public esp32_ble_server::BLEServiceComponent,
	public esp32_ble::GATTsEventHandler
	{
 public:
  IrkEnrollmentComponent() {}
  void dump_config() override;
  void loop() override;
  void setup() override;
  void set_latest_irk(text_sensor::TextSensor *latest_irk) { latest_irk_ = latest_irk; }


  float get_setup_priority() const;
    void gatts_event_handler(esp_gatts_cb_event_t event, esp_gatt_if_t gatts_if,
                           esp_ble_gatts_cb_param_t *param) override;

  void start() override;
  void stop() override;


 protected:
  esp32_ble_server::BLEService *service_{nullptr};
  text_sensor::TextSensor *latest_irk_{nullptr};
};

}  // namespace irk_enrollment
}  // namespace esphome

#endif
