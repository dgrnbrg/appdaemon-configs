#include "irk_enrollment.h"
#include "esphome/components/esp32_ble/ble.h"
#include "esphome/core/application.h"
#include "esphome/core/log.h"
#include "esphome/core/version.h"


#ifdef USE_ESP32

#include <nvs_flash.h>
#include <freertos/FreeRTOSConfig.h>
#include <esp_bt_main.h>
#include <esp_bt.h>
#include <freertos/task.h>
#include <esp_gap_ble_api.h>
#include <esp_gatts_api.h>
#include <esp_bt_defs.h>


namespace esphome {
namespace irk_enrollment {

static const char *const TAG = "irk_enrollment.component";

constexpr char hexmap[] = {'0', '1', '2', '3', '4', '5', '6', '7',
                           '8', '9', 'a', 'b', 'c', 'd', 'e', 'f'};

static std::string hexStr(unsigned char *data, int len)
{
  std::string s(len * 2, ' ');
  for (int i = 0; i < len; ++i) {
    s[2 * i]     = hexmap[(data[len-1-i] & 0xF0) >> 4];
    s[2 * i + 1] = hexmap[data[len-1-i] & 0x0F];
  }
  return s;
}

void IrkEnrollmentComponent::setup() {
  auto service_uuid = esphome::esp32_ble::ESPBTUUID::from_uint16(0x1812);
  esp32_ble_server::global_ble_server->create_service(service_uuid, true);
  this->service_ = esp32_ble_server::global_ble_server->get_service(service_uuid);
  esp32_ble::global_ble->advertising_add_service_uuid(service_uuid);
  // TODO seems like the below configuration is unneeded, but need to confirm with a "clean" device
  //esp_ble_auth_req_t auth_req = ESP_LE_AUTH_BOND; //bonding with peer device after authentication
  //uint8_t key_size = 16;      //the key size should be 7~16 bytes
  //uint8_t init_key = ESP_BLE_ID_KEY_MASK;
  //uint8_t rsp_key = ESP_BLE_ID_KEY_MASK;
  //esp_ble_gap_set_security_param(ESP_BLE_SM_AUTHEN_REQ_MODE, &auth_req, sizeof(uint8_t));
  //esp_ble_gap_set_security_param(ESP_BLE_SM_MAX_KEY_SIZE, &key_size, sizeof(uint8_t));
  //esp_ble_gap_set_security_param(ESP_BLE_SM_SET_INIT_KEY, &init_key, sizeof(uint8_t));
  //esp_ble_gap_set_security_param(ESP_BLE_SM_SET_RSP_KEY, &rsp_key, sizeof(uint8_t));
  //delay(200);  // NOLINT
}

void IrkEnrollmentComponent::dump_config() {
  ESP_LOGCONFIG(TAG, "ESP32 IRK Enrollment:");
  LOG_TEXT_SENSOR(" ", "Latest IRK", this->latest_irk_);
}

void IrkEnrollmentComponent::loop() {
	//ESP_LOGD(TAG, "  dumping bonds:");
	int dev_num = esp_ble_get_bond_device_num();
    if (dev_num > 1) {
        ESP_LOGW(TAG, "We have %d bonds, where we expect to only ever have 0 or 1", dev_num);
    }
	esp_ble_bond_dev_t bond_devs[dev_num];
	esp_ble_get_bond_device_list(&dev_num, bond_devs);
	for (int i = 0; i < dev_num; i++) {
		ESP_LOGI(TAG, "    remote DB_ADDR: %08x%04x",
			(bond_devs[i].bd_addr[0] << 24) + (bond_devs[i].bd_addr[1] << 16) + (bond_devs[i].bd_addr[2] << 8) +
			bond_devs[i].bd_addr[3],
			(bond_devs[i].bd_addr[4] << 8) + bond_devs[i].bd_addr[5]);
        auto irkStr = hexStr((unsigned char *) &bond_devs[i].bond_key.pid_key.irk, 16);
		ESP_LOGI(TAG, "      irk: %s", irkStr.c_str());
        if (this->latest_irk_ != nullptr && this->latest_irk_->get_state() != irkStr) {
            this->latest_irk_->publish_state(irkStr);
        }
        esp_ble_gap_disconnect(bond_devs[i].bd_addr);
        esp_ble_remove_bond_device(bond_devs[i].bd_addr);
        ESP_LOGI(TAG, "  Disconnected and removed bond");
	}
}
void IrkEnrollmentComponent::start() {}
void IrkEnrollmentComponent::stop() {}
float IrkEnrollmentComponent::get_setup_priority() const { return setup_priority::AFTER_BLUETOOTH; }


void IrkEnrollmentComponent::gatts_event_handler(esp_gatts_cb_event_t event, esp_gatt_if_t gatts_if,
		esp_ble_gatts_cb_param_t *param) {
	//ESP_LOGD(TAG, "in gatts event handler");
	switch (event) {
	case ESP_GATTS_CONNECT_EVT:
		//start security connect with peer device when receive the connect event sent by the master.
		esp_ble_set_encryption(param->connect.remote_bda, ESP_BLE_SEC_ENCRYPT_MITM);
		//ESP_LOGD(TAG, "  connect evt");
		break;
	case ESP_GAP_BLE_KEY_EVT:
		//shows the ble key info share with peer device to the user.
		//ESP_LOGI(TAG, "key type = %s", esp_key_type_to_str(param->ble_security.ble_key.key_type));
		//ESP_LOGD(TAG, "  ble key evt");
		break;
	case ESP_GAP_BLE_AUTH_CMPL_EVT: {
		//ESP_LOGD(TAG, "  auth cmpl evt");
		//esp_bd_addr_t bd_addr;
		//memcpy(bd_addr, param->ble_security.auth_cmpl.bd_addr,
		//		sizeof(esp_bd_addr_t));
		//ESP_LOGI(TAG, "remote BD_ADDR: %08x%04x",
		//		(bd_addr[0] << 24) + (bd_addr[1] << 16) + (bd_addr[2] << 8) +
		//		bd_addr[3],
		//		(bd_addr[4] << 8) + bd_addr[5]);
		//ESP_LOGI(TAG, "address type = %d",
		//		param->ble_security.auth_cmpl.addr_type);
		//ESP_LOGI(TAG, "pair status = %s",
		//		param->ble_security.auth_cmpl.success ? "success" : "fail");
		break;
    default:
		//ESP_LOGD(TAG, "  other evt");
        break;
	}
	}
}


}  // namespace irk_enrollment
}  // namespace esphome

#endif
