#pragma once

#ifdef USE_ARDUINO
#include "mbedtls/aes.h"
#include "mbedtls/base64.h"
#endif

#ifdef USE_ESP_IDF
#define MBEDTLS_AES_ALT 1
#include <aes_alt.h>
#endif

//#ifdef USE_ARDUINO
//#elif defined(USE_ESP_IDF)
//#endif

int bt_encrypt_be(const uint8_t *key, const uint8_t *plaintext, uint8_t *enc_data) {
    mbedtls_aes_context s = {
        0
#ifdef USE_ESP_IDF
        , 0, 0
#endif
    };
    mbedtls_aes_init(&s);

    if (mbedtls_aes_setkey_enc(&s, key, 128) != 0) {
        mbedtls_aes_free(&s);
        return -1;
    }

    if (mbedtls_aes_crypt_ecb(&s,
#ifdef USE_ARDUINO
                MBEDTLS_AES_ENCRYPT,
#elif defined(USE_ESP_IDF)
                ESP_AES_ENCRYPT,
#endif
                plaintext, enc_data) != 0) {
        mbedtls_aes_free(&s);
        return -1;
    }

    mbedtls_aes_free(&s);
    return 0;
}

struct encryption_block {
    uint8_t key[16];
    uint8_t plain_text[16];
    uint8_t cipher_text[16];
};

bool ble_ll_resolv_rpa(const uint8_t *rpa, const uint8_t *irk) {
    struct encryption_block ecb;

    auto irk32 = (const uint32_t *)irk;
    auto key32 = (uint32_t *)&ecb.key[0];
    auto pt32 = (uint32_t *)&ecb.plain_text[0];

    key32[0] = irk32[0];
    key32[1] = irk32[1];
    key32[2] = irk32[2];
    key32[3] = irk32[3];

    pt32[0] = 0;
    pt32[1] = 0;
    pt32[2] = 0;
    pt32[3] = 0;

    ecb.plain_text[15] = rpa[3];
    ecb.plain_text[14] = rpa[4];
    ecb.plain_text[13] = rpa[5];

    auto err = bt_encrypt_be(ecb.key, ecb.plain_text, ecb.cipher_text);

    if (err) {
        ESP_LOGW("irk_resolve", "AES failure");
	return false;
    }

    if (ecb.cipher_text[15] != rpa[0] || ecb.cipher_text[14] != rpa[1] || ecb.cipher_text[13] != rpa[2]) return false;

    // Serial.printf("RPA resolved %d %02x%02x%02x %02x%02x%02x\n", err, rpa[0], rpa[1], rpa[2], ecb.cipher_text[15], ecb.cipher_text[14], ecb.cipher_text[13]);

    return true;
}

static std::vector<std::vector<uint8_t>> irk_prefilters;


#ifdef USE_ESP_IDF
/** Output buffer too small. */
#define MBEDTLS_ERR_BASE64_BUFFER_TOO_SMALL               -0x002A
/** Invalid character in input. */
#define MBEDTLS_ERR_BASE64_INVALID_CHARACTER              -0x002C
/** Byte Reading Macros
 *
 * Given a multi-byte integer \p x, MBEDTLS_BYTE_n retrieves the n-th
 * byte from x, where byte 0 is the least significant byte.
 */
#define MBEDTLS_BYTE_0(x) ((uint8_t) ((x)         & 0xff))
#define MBEDTLS_BYTE_1(x) ((uint8_t) (((x) >>  8) & 0xff))
#define MBEDTLS_BYTE_2(x) ((uint8_t) (((x) >> 16) & 0xff))

/* Return 0xff if low <= c <= high, 0 otherwise.
 *
 * Constant flow with respect to c.
 */
unsigned char mbedtls_ct_uchar_mask_of_range(unsigned char low,
                                             unsigned char high,
                                             unsigned char c)
{
    /* low_mask is: 0 if low <= c, 0x...ff if low > c */
    unsigned low_mask = ((unsigned) c - low) >> 8;
    /* high_mask is: 0 if c <= high, 0x...ff if c > high */
    unsigned high_mask = ((unsigned) high - c) >> 8;
    return ~(low_mask | high_mask) & 0xff;
}


signed char mbedtls_ct_base64_dec_value(unsigned char c)
{
    unsigned char val = 0;
    /* For each range of digits, if c is in that range, mask val with
     * the corresponding value. Since c can only be in a single range,
     * only at most one masking will change val. Set val to one plus
     * the desired value so that it stays 0 if c is in none of the ranges. */
    val |= mbedtls_ct_uchar_mask_of_range('A', 'Z', c) & (c - 'A' +  0 + 1);
    val |= mbedtls_ct_uchar_mask_of_range('a', 'z', c) & (c - 'a' + 26 + 1);
    val |= mbedtls_ct_uchar_mask_of_range('0', '9', c) & (c - '0' + 52 + 1);
    val |= mbedtls_ct_uchar_mask_of_range('+', '+', c) & (c - '+' + 62 + 1);
    val |= mbedtls_ct_uchar_mask_of_range('/', '/', c) & (c - '/' + 63 + 1);
    /* At this point, val is 0 if c is an invalid digit and v+1 if c is
     * a digit with the value v. */
    return val - 1;
}

/*
 * Decode a base64-formatted buffer
 */
int mbedtls_base64_decode(unsigned char *dst, size_t dlen, size_t *olen,
                          const unsigned char *src, size_t slen)
{
    size_t i; /* index in source */
    size_t n; /* number of digits or trailing = in source */
    uint32_t x; /* value accumulator */
    unsigned accumulated_digits = 0;
    unsigned equals = 0;
    int spaces_present = 0;
    unsigned char *p;

    /* First pass: check for validity and get output length */
    for (i = n = 0; i < slen; i++) {
        /* Skip spaces before checking for EOL */
        spaces_present = 0;
        while (i < slen && src[i] == ' ') {
            ++i;
            spaces_present = 1;
        }

        /* Spaces at end of buffer are OK */
        if (i == slen) {
            break;
        }

        if ((slen - i) >= 2 &&
            src[i] == '\r' && src[i + 1] == '\n') {
            continue;
        }

        if (src[i] == '\n') {
            continue;
        }

        /* Space inside a line is an error */
        if (spaces_present) {
            return MBEDTLS_ERR_BASE64_INVALID_CHARACTER;
        }

        if (src[i] > 127) {
            return MBEDTLS_ERR_BASE64_INVALID_CHARACTER;
        }

        if (src[i] == '=') {
            if (++equals > 2) {
                return MBEDTLS_ERR_BASE64_INVALID_CHARACTER;
            }
        } else {
            if (equals != 0) {
                return MBEDTLS_ERR_BASE64_INVALID_CHARACTER;
            }
            if (mbedtls_ct_base64_dec_value(src[i]) < 0) {
                return MBEDTLS_ERR_BASE64_INVALID_CHARACTER;
            }
        }
        n++;
    }

    if (n == 0) {
        *olen = 0;
        return 0;
    }

    /* The following expression is to calculate the following formula without
     * risk of integer overflow in n:
     *     n = ( ( n * 6 ) + 7 ) >> 3;
     */
    n = (6 * (n >> 3)) + ((6 * (n & 0x7) + 7) >> 3);
    n -= equals;

    if (dst == NULL || dlen < n) {
        *olen = n;
        return MBEDTLS_ERR_BASE64_BUFFER_TOO_SMALL;
    }

    equals = 0;
    for (x = 0, p = dst; i > 0; i--, src++) {
        if (*src == '\r' || *src == '\n' || *src == ' ') {
            continue;
        }

        x = x << 6;
        if (*src == '=') {
            ++equals;
        } else {
            x |= mbedtls_ct_base64_dec_value(*src);
        }

        if (++accumulated_digits == 4) {
            accumulated_digits = 0;
            *p++ = MBEDTLS_BYTE_2(x);
            if (equals <= 1) {
                *p++ = MBEDTLS_BYTE_1(x);
            }
            if (equals <= 0) {
                *p++ = MBEDTLS_BYTE_0(x);
            }
        }
    }

    *olen = p - dst;

    return 0;
}
#endif
