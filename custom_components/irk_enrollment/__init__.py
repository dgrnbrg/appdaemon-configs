import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import esp32_ble_server, esp32_ble, text_sensor
from esphome.const import CONF_ID, ENTITY_CATEGORY_DIAGNOSTIC
from esphome.components.esp32 import add_idf_sdkconfig_option

AUTO_LOAD = ["esp32_ble_server", "text_sensor"]
CODEOWNERS = ["@dgrnbrg"]
CONFLICTS_WITH = ["esp32_ble_beacon"]
DEPENDENCIES = ["esp32", "text_sensor"]

CONF_BLE_SERVER_ID = "ble_server_id"
CONF_LATEST_IRK = "latest_irk"
CONF_PREPARE = "enroll_button"

irk_enrollment_ns = cg.esphome_ns.namespace("irk_enrollment")
IrkEnrollmentComponent = irk_enrollment_ns.class_("IrkEnrollmentComponent",
    cg.Component,
    esp32_ble.GATTsEventHandler,
    esp32_ble_server.BLEServiceComponent,
)

CONFIG_SCHEMA = (
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(IrkEnrollmentComponent),
            cv.GenerateID(CONF_BLE_SERVER_ID): cv.use_id(esp32_ble_server.BLEServer),
            cv.GenerateID(esp32_ble.CONF_BLE_ID): cv.use_id(esp32_ble.ESP32BLE),
            cv.Optional(CONF_LATEST_IRK): text_sensor.text_sensor_schema(
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
                icon="mdi:cellphone-key",
            ),
        }
    )
    .extend(cv.COMPONENT_SCHEMA)
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)

    ble_server = await cg.get_variable(config[CONF_BLE_SERVER_ID])
    cg.add(ble_server.register_service_component(var))

    ble_master = await cg.get_variable(config[esp32_ble.CONF_BLE_ID])
    cg.add(ble_master.register_gatts_event_handler(var))

    if CONF_LATEST_IRK in config:
        latest_irk = await text_sensor.new_text_sensor(config[CONF_LATEST_IRK])
        cg.add(var.set_latest_irk(latest_irk))
