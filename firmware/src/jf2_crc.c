#include "jf2_state.h"
#include <stdio.h>

uint32_t jf2_crc32(const void *data, size_t len, uint32_t previous)
{
    const uint8_t *p = data;
    uint32_t crc = ~previous;
    for (size_t i = 0; i < len; ++i) {
        crc ^= p[i];
        for (unsigned b = 0; b < 8; ++b)
            crc = (crc >> 1) ^ ((crc & 1u) ? UINT32_C(0xedb88320) : 0u);
    }
    return ~crc;
}

uint32_t jf2_operations_crc(const Jf2Op *ops, size_t count)
{
    uint32_t crc = 0;
    for (size_t i = 0; i < count; ++i) {
        const Jf2Op *op = &ops[i];
        char token[16];
        int len;
        if (op->tag)
            len = snprintf(token, sizeof token, "+%u:%u:%u@%u\n",
                           op->chip, op->x, op->y, op->tag);
        else
            len = snprintf(token, sizeof token, "-%u:%u:%u\n",
                           op->chip, op->x, op->y);
        crc = jf2_crc32(token, (size_t)len, crc);
    }
    return crc;
}
