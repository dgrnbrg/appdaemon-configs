from esphome import pins
import esphome.codegen as cg
from esphome import automation
import esphome.config_validation as cv
from esphome.components import i2c, sensor
from esphome.const import CONF_ID
import math

DEPENDENCIES = ['i2c']

CONF_I2C_ADDR = 0x1A
CONF_VOLUME = "volume"

nau8810_ns = cg.esphome_ns.namespace('nau8810')
NAU8810Component = nau8810_ns.class_('NAU8810Component', cg.Component, i2c.I2CDevice)
SetSpeakerVolumeAction = nau8810_ns.class_('SetSpeakerVolumeAction', automation.Action)

CONFIG_SCHEMA = cv.Schema({
    cv.GenerateID(): cv.declare_id(NAU8810Component),
}).extend(cv.COMPONENT_SCHEMA).extend(i2c.i2c_device_schema(CONF_I2C_ADDR))

@automation.register_action("nau8810.set_speaker_volume", SetSpeakerVolumeAction,
    cv.Schema(
        {
            cv.Required(CONF_ID): cv.use_id(NAU8810Component),
            cv.Required(CONF_VOLUME): cv.templatable(cv.int_range(0,255)),
        }
    )
)
async def nau8810_set_speaker_volume_to_code(config, action_id, template_arg, args):
    paren = await cg.get_variable(config[CONF_ID])
    var = cg.new_Pvariable(action_id, template_arg, paren)
    template_ = await cg.templatable(config[CONF_VOLUME], args, int)
    cg.add(var.set_volume(template_))
    return var

async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await i2c.register_i2c_device(var, config)


