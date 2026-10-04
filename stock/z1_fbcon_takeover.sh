#!/system/bin/sh
# Silence framebuffer-console text after Android takes over /dev/fb0, without
# removing the serial console used for boot diagnostics.
vtconsole_root=${Z1_VTCONSOLE_ROOT:-/sys/class/vtconsole}

for vtconsole in "$vtconsole_root"/vtcon*; do
    [ -d "$vtconsole" ] || continue
    [ -r "$vtconsole/name" ] || continue
    IFS= read -r name < "$vtconsole/name" || continue

    case "$name" in
        *"frame buffer device"*)
            [ -r "$vtconsole/bind" ] || exit 1
            IFS= read -r bound < "$vtconsole/bind" || exit 1
            case "$bound" in
                0) continue ;;
                1) ;;
                *) exit 1 ;;
            esac

            printf '0\n' > "$vtconsole/bind" || exit 1
            IFS= read -r bound < "$vtconsole/bind" || exit 1
            [ "$bound" = 0 ] || exit 1
            continue
            ;;
    esac
done

# Some builds have no VT framebuffer console. In that case there is nothing
# to detach; leave Android startup and the serial console untouched.
exit 0
