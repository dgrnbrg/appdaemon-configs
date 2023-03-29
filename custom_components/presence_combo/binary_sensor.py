import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import binary_sensor
from esphome.const import CONF_ID

CODEOWNERS = ["@dgrnbrg"]

presence_combo_ns = cg.esphome_ns.namespace("presence_combo")
PresenceComboComponent = presence_combo_ns.class_("PresenceComboComponent",
        binary_sensor.BinarySensor,
        cg.Component,
)

CONF_IDS = "ids"

CONFIG_SCHEMA = (
    binary_sensor.BINARY_SENSOR_SCHEMA
    .extend(
        {
            cv.GenerateID(): cv.declare_id(PresenceComboComponent),
            cv.Required(CONF_IDS): cv.All(
                cv.ensure_list(cv.use_id(binary_sensor.BinarySensor)),
                cv.Length(min=1),
            ),
        }
    )
    .extend(cv.COMPONENT_SCHEMA)
)

async def to_code(config):
    var = await binary_sensor.new_binary_sensor(config)
    await cg.register_component(var, config)
    for x in config[CONF_IDS]:
        child_var = await cg.get_variable(x)
        cg.add(var.add_child_sensor(child_var))
