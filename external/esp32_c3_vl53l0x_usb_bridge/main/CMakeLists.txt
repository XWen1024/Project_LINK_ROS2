idf_component_register(
    SRCS
        "app_main.c"
        "vl53l0x.c"
    INCLUDE_DIRS "."
    REQUIRES esp_driver_i2c esp_timer
)
