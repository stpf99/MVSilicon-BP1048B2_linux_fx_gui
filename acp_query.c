// acp_query.c — odczyt stanu modułów DSP MVSilicon BP1048B2
// INTERFEJS 4 TYLKO — nie dotyka interfejsów audio (0-3)!
//
// Użycie:
//   ./acp_query fw          → firmware / chip / wersje efektów
//   ./acp_query cpu         → obciążenie CPU i pamięci
//   ./acp_query <hex_mid>   → surowy odczyt modułu (np. A2 = Music Pre EQ)
//
// Wyjście: ciąg bajtów odpowiedzi w formacie hex rozdzielonych przecinkami
//          (parsowany przez acp_gui_qt2.py → acp_query_device)
//
// Kompilacja: gcc -O2 -o acp_query acp_query.c -lusb-1.0

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <libusb-1.0/libusb.h>

#define VID         0x8888
#define PID         0x1719
#define IFACE       4
#define EP_IN       0x83          /* HID interrupt IN */
#define TIMEOUT_MS  400
#define CTRL_TO     500

#define SYNC0       0xA5
#define SYNC1       0x5A
#define ALL_PARAM   0xFF
#define SYNC_TAIL   0x16

static libusb_device_handle *open_dev(void)
{
    libusb_device_handle *h = libusb_open_device_with_vid_pid(NULL, VID, PID);
    if (!h) {
        fprintf(stderr, "Device not found (8888:1719)\n");
        return NULL;
    }
    return h;
}

static int claim_iface4(libusb_device_handle *h)
{
    if (libusb_kernel_driver_active(h, IFACE) == 1)
        libusb_detach_kernel_driver(h, IFACE);
    int r = libusb_claim_interface(h, IFACE);
    if (r < 0)
        fprintf(stderr, "claim_interface(%d) failed: %s\n", IFACE, libusb_error_name(r));
    return r;
}

static void cleanup(libusb_device_handle *h)
{
    libusb_release_interface(h, IFACE);
    libusb_attach_kernel_driver(h, IFACE);
    libusb_close(h);
}

/* Wysyła ramkę query: A5 5A [mid] 01 FF 16  i czyta odpowiedź z EP 0x83 */
static int do_query(libusb_device_handle *h, unsigned char mid,
                    unsigned char *resp, int resp_max)
{
    unsigned char pkt[64] = {0};
    pkt[0] = SYNC0;
    pkt[1] = SYNC1;
    pkt[2] = mid;
    pkt[3] = 1;             /* len = 1 (tylko ALL_PARAM) */
    pkt[4] = ALL_PARAM;
    pkt[5] = SYNC_TAIL;

    int r = libusb_control_transfer(h, 0x21, 0x09, 0x0200, IFACE,
                                    pkt, 64, CTRL_TO);
    if (r < 0) {
        fprintf(stderr, "control_transfer failed: %s\n", libusb_error_name(r));
        return -1;
    }

    usleep(40000);          /* ~40 ms na odpowiedź firmware */

    int transferred = 0;
    r = libusb_interrupt_transfer(h, EP_IN, resp, resp_max,
                                  &transferred, TIMEOUT_MS);
    if (r < 0) {
        fprintf(stderr, "interrupt_transfer IN failed: %s\n", libusb_error_name(r));
        return -1;
    }
    return transferred;
}

static void print_hex_csv(const unsigned char *buf, int len)
{
    for (int i = 0; i < len; i++) {
        if (i) putchar(',');
        printf("%02X", buf[i]);
    }
    putchar('\n');
}

int main(int argc, char **argv)
{
    if (argc < 2) {
        fprintf(stderr,
            "Uzycie:\n"
            "  %s fw              odczyt firmware / chip / FX version\n"
            "  %s cpu             odczyt CPU / memory usage\n"
            "  %s <hex_module>    surowy odczyt (np. A2, 00, 01)\n",
            argv[0], argv[0], argv[0]);
        return 1;
    }

    unsigned char mid;
    if (strcmp(argv[1], "fw") == 0)
        mid = 0x00;
    else if (strcmp(argv[1], "cpu") == 0)
        mid = 0x01;
    else {
        unsigned int v;
        if (sscanf(argv[1], "%x", &v) != 1 || v > 0xFF) {
            fprintf(stderr, "Nieprawidlowy module id: %s\n", argv[1]);
            return 1;
        }
        mid = (unsigned char)v;
    }

    if (libusb_init(NULL) < 0) {
        fprintf(stderr, "libusb_init fail\n");
        return 1;
    }

    libusb_device_handle *h = open_dev();
    if (!h) {
        libusb_exit(NULL);
        return 1;
    }
    if (claim_iface4(h) < 0) {
        libusb_close(h);
        libusb_exit(NULL);
        return 1;
    }

    unsigned char resp[64] = {0};
    int n = do_query(h, mid, resp, sizeof(resp));
    cleanup(h);
    libusb_exit(NULL);

    if (n <= 0)
        return 1;

    /* Wyświetl tylko sensowną część odpowiedzi (do pierwszego zera po SYNC_TAIL
       albo pełne 64 jeśli firmware padło więcej). Python i tak parsuje od A5 5A. */
    int out_len = n;
    /* Przytnij trailing zera poza minimum ramki */
    while (out_len > 6 && resp[out_len - 1] == 0)
        out_len--;

    print_hex_csv(resp, out_len);
    return 0;
}
