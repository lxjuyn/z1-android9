savedcmd_z1_key_gpio_hunt.mod := printf '%s\n'   z1_key_gpio_hunt.o | awk '!x[$$0]++ { print("./"$$0) }' > z1_key_gpio_hunt.mod
