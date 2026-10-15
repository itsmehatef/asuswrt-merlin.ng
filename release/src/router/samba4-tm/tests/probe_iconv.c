/* Probe packaged glibc's conversion availability without host-library fallback. */
#include <errno.h>
#include <iconv.h>
#include <stdio.h>
#include <string.h>

static int convert_sample(const char *to, const char *from, const char *input,
                          size_t input_size, const char *expected, size_t expected_size)
{
    iconv_t converter = iconv_open(to, from);
    char source[32], output[64], *in = source, *out = output;
    size_t in_left = input_size, out_left = sizeof(output), result;
    int passed;
    if (converter == (iconv_t)-1)
        return 1;
    memcpy(source, input, input_size);
    result = iconv(converter, &in, &in_left, &out, &out_left);
    passed = result != (size_t)-1 && in_left == 0
             && sizeof(output) - out_left == expected_size
             && memcmp(output, expected, expected_size) == 0;
    printf("conversion sample (%s <- %s): %s\n", to, from, passed ? "PASS" : "FAIL");
    iconv_close(converter);
    return passed ? 0 : 1;
}

int main(void)
{
    const char *from[] = { "CP850", "CP850", "CP850", "UTF-8", "UTF-8" };
    const char *to[] = { "UCS-2LE", "UTF-16LE", "UTF-8", "UTF-16LE", "ASCII" };
    unsigned i;
    int failures = 0;
    for (i = 0; i < sizeof(from) / sizeof(from[0]); i++) {
        iconv_t converter;
        errno = 0;
        converter = iconv_open(to[i], from[i]);
        printf("iconv_open(%s <- %s): %s errno=%d\n", to[i], from[i],
               converter == (iconv_t)-1 ? "UNAVAILABLE" : "available", errno);
        if (converter == (iconv_t)-1)
            failures++;
        else
            iconv_close(converter);
    }
    failures += convert_sample("UCS-2LE", "CP850", "caf\x82", 4,
                               "c\0a\0f\0\xE9\0", 8);
    failures += convert_sample("UTF-16LE", "UTF-8", "\xF0\x9F\x92\xBE", 4,
                               "\x3D\xD8\xBE\xDC", 4);
    return failures ? 1 : 0;
}
