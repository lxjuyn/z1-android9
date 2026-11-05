#!/bin/bash
# Pure log predicates: no device access. Contracts: mtk_da_handler.py da_w/da_erase.
mtk_normalize_output() {
  printf '%s\n' "$1" | tr '\r' '\n' | sed $'s/\033\\[[0-9;]*[A-Za-z]//g'
}

mtk_output_failed() {
  mtk_normalize_output "$1" | grep -qiE '(fail(ed|ure)?|USBError|Traceback|Exception|(^|[^[:alnum:]_])error([^[:alnum:]_]|$)|timed out|timeout|aborted|disconnect|no device|nothing connected|no preloader|not (found|detected)|couldn.t|cannot|can.t)'
}

mtk_output_connected() {
  local out
  out=$(mtk_normalize_output "$1")
  # Transfer/DA evidence wins over a later disconnect or timeout.
  if printf '%s\n' "$out" | grep -qiE '(Write:|Read:|Erase:|Format:|Wrote |Formatted |Failed to (write|format)|sdmmc_write_data|Data ack|Handling da commands|DALegacy|DAXFlash|m_emmc_|sync done|(^| - )device (detected|found)|BROM mode|preloader.*(CPU|connected)|connection established)'; then
    return 0
  fi
  # Only an explicitly clean no-device transcript may skip recovery. Unknown,
  # empty and truncated output is not proof that no destructive I/O occurred.
  if printf '%s\n' "$out" | grep -qiE '(nothing connected|no preloader|preloader not (found|detected)|no device|device not found|waiting for (device|preloader)|please (connect|reconnect))'; then
    return 1
  fi
  return 0
}

mtk_write_succeeded() {
  local out="$1" rc="$2" expected="$3" last prefix rest
  [ "$rc" -eq 0 ] || return 1
  mtk_output_failed "$out" && return 1
  last=$(mtk_normalize_output "$out" | sed '/^[[:space:]]*$/d' | tail -n 1)
  prefix="Wrote $expected to sector "
  [[ "$last" == "$prefix"* ]] || return 1
  rest=${last#"$prefix"}
  [[ "$rest" =~ ^[0-9]+\ with\ sector\ count\ [1-9][0-9]*\.$ ]]
}

mtk_erase_succeeded() {
  local out="$1" rc="$2" last
  [ "$rc" -eq 0 ] || return 1
  mtk_output_failed "$out" && return 1
  last=$(mtk_normalize_output "$out" | sed '/^[[:space:]]*$/d' | tail -n 1)
  [[ "$last" =~ ^Formatted\ sector\ [0-9]+\ with\ sector\ count\ [1-9][0-9]*\.$ ]]
}
