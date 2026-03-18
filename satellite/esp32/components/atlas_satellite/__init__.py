# Atlas Satellite — ESPHome custom component registration
#
# This file tells ESPHome about the atlas_satellite component.
# The actual implementation lives in atlas_satellite.h (C++).
#
# Configuration schema for the component YAML:
#   atlas_satellite:
#     server_url: "ws://server:5100/ws/satellite"
#     device_name: "kitchen"
#     hardware: "generic"
#     microphone: mic
#     speaker: spk
#     status_led: status_led
#     sample_rate: 16000
#     bits_per_sample: 16

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.const import CONF_ID

# TODO: Implement ESPHome component registration.
# This is a stub — the full implementation requires the ESPHome
# build toolchain. The structure below outlines the expected
# configuration schema and code generation.
#
# When implemented, this file should:
#   1. Define the CONFIG_SCHEMA with all fields above
#   2. Register the C++ component class
#   3. Generate the C++ setup code from YAML config
#
# Reference: https://esphome.io/components/external_components

DEPENDENCIES = ["wifi"]
AUTO_LOAD = ["json"]

CONF_SERVER_URL = "server_url"
CONF_DEVICE_NAME = "device_name"
CONF_HARDWARE = "hardware"
CONF_MICROPHONE = "microphone"
CONF_SPEAKER = "speaker"
CONF_STATUS_LED = "status_led"
CONF_SAMPLE_RATE = "sample_rate"
CONF_BITS_PER_SAMPLE = "bits_per_sample"

# atlas_satellite_ns = cg.esphome_ns.namespace("atlas_satellite")
# AtlasSatelliteComponent = atlas_satellite_ns.class_(
#     "AtlasSatelliteComponent", cg.Component
# )
#
# CONFIG_SCHEMA = cv.Schema({
#     cv.GenerateID(): cv.declare_id(AtlasSatelliteComponent),
#     cv.Required(CONF_SERVER_URL): cv.url,
#     cv.Optional(CONF_DEVICE_NAME, default="atlas-satellite"): cv.string,
#     cv.Optional(CONF_HARDWARE, default="generic"): cv.string,
#     cv.Required(CONF_MICROPHONE): cv.use_id(...),
#     cv.Required(CONF_SPEAKER): cv.use_id(...),
#     cv.Optional(CONF_STATUS_LED): cv.use_id(...),
#     cv.Optional(CONF_SAMPLE_RATE, default=16000): cv.int_,
#     cv.Optional(CONF_BITS_PER_SAMPLE, default=16): cv.int_,
# }).extend(cv.COMPONENT_SCHEMA)
#
# async def to_code(config):
#     var = cg.new_Pvariable(config[CONF_ID])
#     await cg.register_component(var, config)
#     cg.add(var.set_server_url(config[CONF_SERVER_URL]))
#     cg.add(var.set_device_name(config[CONF_DEVICE_NAME]))
#     cg.add(var.set_hardware(config[CONF_HARDWARE]))
#     mic = await cg.get_variable(config[CONF_MICROPHONE])
#     cg.add(var.set_microphone(mic))
#     spk = await cg.get_variable(config[CONF_SPEAKER])
#     cg.add(var.set_speaker(spk))
#     if CONF_STATUS_LED in config:
#         led = await cg.get_variable(config[CONF_STATUS_LED])
#         cg.add(var.set_status_led(led))
