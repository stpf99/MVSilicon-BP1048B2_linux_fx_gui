// acp_send.c — sender DSP MVSilicon BP1048B2
// INTERFACE 4 + control transfer 0x21/0x09
//
// Użycie:
//   ./acp_send "A5 5A 82 0B FF 01 00 6C EE 03 00 02 00 64 00 16"  ← pełna ramka ACP
//   ./acp_send "1A 01 01"        → prosty protokół 3-bajtowy (mute ON)
//   ./acp_send mute_on
//   ./acp_send mute_off
//   ./acp_send vol 80
//   ./acp_send probe
//
// Kompilacja: gcc -O2 -o acp_send acp_send.c -lusb-1.0

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <libusb-1.0/libusb.h>

#define VID     0x8888
#define PID     0x1719
#define IFACE   4
#define TIMEOUT 1500

#define CMD_VOLUME   0x13
#define CMD_MUTE     0x1A

static libusb_device_handle *open_dev(void) {
    libusb_device_handle *h = libusb_open_device_with_vid_pid(NULL, VID, PID);
    if (!h) { fprintf(stderr, "Device not found (8888:1719)\n"); return NULL; }
    return h;
}

static int detach_and_claim(libusb_device_handle *h) {
    for (int i = 0; i <= 5; i++) {
        if (libusb_kernel_driver_active(h, i) == 1)
            libusb_detach_kernel_driver(h, i);
    }
    int r = libusb_claim_interface(h, IFACE);
    if (r < 0) fprintf(stderr, "claim_interface(%d) failed: %s\n", IFACE, libusb_error_name(r));
    return r;
}

static int send_raw(libusb_device_handle *h, unsigned char *pkt, int len) {
    unsigned char buf[64] = {0};
    int copy_len = len > 64 ? 64 : len;
    memcpy(buf, pkt, copy_len);
    // Zawsze padujemy do 64 bajtów (control transfer wymaga)
    int r = libusb_control_transfer(h, 0x21, 0x09, 0x0200, IFACE, buf, 64, TIMEOUT);
    if (r < 0) fprintf(stderr, "send error: %s\n", libusb_error_name(r));
    return r;
}

static void cleanup(libusb_device_handle *h) {
    libusb_release_interface(h, IFACE);
    for (int i = 0; i <= 5; i++) {
        libusb_attach_kernel_driver(h, i);
    }
    libusb_close(h);
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr,
            "Uzycie:\n"
            "  %s <hex_bytes>     np: 'A5 5A 82 0B FF ... 16'  (pełna ramka ACP)\n"
            "  %s mute_on / mute_off\n"
            "  %s vol <0-100>\n"
            "  %s probe           skanuj CMD 0x00–0x30\n",
            argv[0], argv[0], argv[0], argv[0]);
        return 1;
    }

    if (libusb_init(NULL) < 0) { fprintf(stderr,"libusb_init fail\n"); return 1; }

    libusb_device_handle *h = open_dev();
    if (!h) { libusb_exit(NULL); return 1; }
    if (detach_and_claim(h) < 0) { libusb_close(h); libusb_exit(NULL); return 1; }

    int r = 0;

    if (strcmp(argv[1],"mute_on")==0) {
        unsigned char pkt[] = {CMD_MUTE, 0x01, 0x01};
        r = send_raw(h, pkt, 3);
        if (r>=0) printf("[OK] MUTE ON\n");
    } else if (strcmp(argv[1],"mute_off")==0) {
        unsigned char pkt[] = {CMD_MUTE, 0x01, 0x00};
        r = send_raw(h, pkt, 3);
        if (r>=0) printf("[OK] MUTE OFF\n");
    } else if (strcmp(argv[1],"vol")==0 && argc>=3) {
        int vol = atoi(argv[2]);
        if (vol<0) vol=0; if(vol>100) vol=100;
        unsigned char pkt[] = {CMD_VOLUME, 0x01, (unsigned char)vol};
        r = send_raw(h, pkt, 3);
        if (r>=0) printf("[OK] VOLUME %d%%\n", vol);
    } else if (strcmp(argv[1],"probe")==0) {
        fprintf(stderr, "PROBE MODE — CMD 0x00–0x30, value=0x01\n");
        for (int cmd=0; cmd<=0x30; cmd++) {
            unsigned char pkt[] = {(unsigned char)cmd, 0x01, 0x01};
            send_raw(h, pkt, 3);
            printf("CMD 0x%02X sent\n", cmd);
            fflush(stdout);
            usleep(300000);
        }
    } else {
        // Tryb raw hex — obsługuje pełne ramki ACP (do 64 bajtów)
        unsigned char pkt[64] = {0};
        const char *hex = argv[1];
        int len = 0;
        while (*hex && len < 64) {
            if (*hex==' '||*hex=='\n'||*hex=='\t'){hex++;continue;}
            unsigned int byte;
            if (sscanf(hex,"%2x",&byte)!=1) break;
            pkt[len++]=(unsigned char)byte; hex+=2;
        }
        if (len == 0) {
            fprintf(stderr, "Brak danych do wysłania\n");
            cleanup(h); libusb_exit(NULL); return 1;
        }
        r = send_raw(h, pkt, len);
        if (r>=0) {
            printf("[OK] Sent %d bytes:", len);
            for (int i=0; i<len; i++) printf(" %02X", pkt[i]);
            printf("\n");
        } else {
            fprintf(stderr,"Send error: %s\n", libusb_error_name(r));
        }
    }

    usleep(100000);
    cleanup(h);
    libusb_exit(NULL);
    return (r < 0) ? 1 : 0;
}
