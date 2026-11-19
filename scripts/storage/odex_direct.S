.syntax unified
.arm
.global _start
.text
_start:
    ldr r0, =block
    mov r1, #0x10000 @ ARM O_DIRECT (0200000 octal)
    mov r7, #5       @ __NR_open
    svc #0
    cmp r0, #0
    movlt r0, #4
    blt exit
    mov r4, r0
    ldr r5, =offsets
    mov r6, #5
next:
    mov r0, r4
    ldr r1, [r5], #4
    mov r2, #0
    mov r7, #19      @ __NR_lseek
    svc #0
    cmp r0, r1
    movne r0, #5
    bne exit
    mov r0, r4
    ldr r1, =page
    mov r2, #4096
    mov r7, #3       @ __NR_read
    svc #0
    cmp r0, #4096
    movne r0, #6
    bne exit
    mov r0, #1
    mov r7, #4       @ __NR_write
    svc #0
    cmp r0, #4096
    movne r0, #7
    bne exit
    subs r6, r6, #1
    bne next
    mov r0, #0
exit:
    mov r7, #1       @ __NR_exit
    svc #0
offsets:
    .word 0x1c0f4000, 0x1c4d1000, 0x1c4d6000, 0x1c4d7000, 0x1c4d8000
block:
    .asciz "/dev/block/mmcblk0p4"
.bss
.balign 4096
page:
    .space 4096
