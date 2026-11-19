# Z1 raw NVRAM read-only extraction

`z1_extract_nvram_readonly.py` derives the two AP configuration files needed
by the Z1 Android radio stack from a local raw `NVRAM` partition image:

- `/data/nvram/APCFG/APRDEB/WIFI`
- `/data/nvram/APCFG/APRDEB/BT_Addr`

The input is a fixed-size Z1 MT6572 layout. Inspection is the default:

```sh
python3 scripts/btgps/nvram/z1_extract_nvram_readonly.py /path/to/NVRAM
```

To create a private staging tree for later transfer to the device:

```sh
python3 scripts/btgps/nvram/z1_extract_nvram_readonly.py \
  /path/to/NVRAM \
  --output-dir /private/tmp/z1-nvram-restore
```

The output contains no `RestoreFlag`, so it cannot make the factory daemon
mistake a two-record diagnostic tree for a complete `/data/nvram` restore.
Do not add the staging tree to a public image or repository.

Before writing anything, the tool verifies:

- both copies of the NVRAM table are byte-identical;
- the whole NVRAM content checksum;
- contiguous and in-bounds record extents;
- the trailing checksums on the WLAN and Bluetooth records; and
- the fixed 512-byte WLAN and 64-byte Bluetooth payload sizes.

IMEI and radio calibration records are checked for integrity but are excluded
from reports and output. Their bounds are covered by the table and whole-image
checksums; their vendor-specific internal formats are not interpreted. The
source file is opened with `O_RDONLY` and `O_NOFOLLOW`; extraction never writes
to it.
