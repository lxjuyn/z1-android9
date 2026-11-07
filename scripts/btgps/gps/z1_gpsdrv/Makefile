obj-m += z1_gpsdrv.o

KDIR ?= /home/lxj/claude/6572/fork_linux
PWD  := $(shell pwd)

all:
	$(MAKE) -C $(KDIR) M=$(PWD) ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf- modules

clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean
