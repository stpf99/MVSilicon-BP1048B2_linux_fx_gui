// acp_send.c — sender DSP MVSilicon BP1048B2
// INTERFEJS 4 TYLKO — nie dotyka interfejsów audio (0-3)!
//
// Użycie:
//   ./acp_send "A5 5A 82 0B FF 01 00 6C EE 03 00 02 00 64 00 16"
//   ./acp_send "1A 01 01"        → prosty protokół 3-bajtowy
//   ./acp_send mute_on / mute_off / vol 80 / probe
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
#define TIMEOUT 500

#define CMD_VOLUME   0x13
#define CMD_MUTE     0x1A

static libusb_device_handle *open_dev(void) {
    libusb_device_handle *h = libusb_open_device_with_vid_pid(NULL, VID, PID);
    if (!h) { fprintf(stderr, "Device not found (8888:1719)\n"); return NULL; }
    return h;
}

static int claim_iface4(libusb_device_handle *h) {
    /* Tylko interfejs 4 (HID control) — NIE dotykamy 0-3 (audio)! */
    if (libusb_kernel_driver_active(h, IFACE) == 1)
        libusb_detach_kernel_driver(h, IFACE);
    int r = libusb_claim_interface(h, IFACE);
    if (r < 0) fprintf(stderr, "claim_interface(%d) failed: %s\n", IFACE, libusb_error_name(r));
    return r;
}

static int send_raw(libusb_device_handle *h, unsigned char *pkt, int len) {
    unsigned char buf[64] = {0};
    int copy_len = len > 64 ? 64 : len;
    memcpy(buf, pkt, copy_len);
    return libusb_control_transfer(h, 0x21, 0x09, 0x0200, IFACE, buf, 64, TIMEOUT);
}

static void cleanup(libusb_device_handle *h) {
    libusb_release_interface(h, IFACE);
    libusb_attach_kernel_driver(h, IFACE);
    libusb_close(h);
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr,
            "Uzycie:\n"
            "  %s <hex_bytes>     np: 'A5 5A 82 0B FF ... 16'\n"
            "  %s mute_on / mute_off\n"
            "  %s vol <0-100>\n"
            "  %s probe\n",
            argv[0], argv[0], argv[0], argv[0]);
        return 1;
    }
    if (libusb_init(NULL) < 0) { fprintf(stderr,"libusb_init fail\n"); return 1; }

    libusb_device_handle *h = open_dev();
    if (!h) { libusb_exit(NULL); return 1; }
    if (claim_iface4(h) < 0) { libusb_close(h); libusb_exit(NULL); return 1; }

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
        for (int cmd=0; cmd<=0x30; cmd++) {
            unsigned char pkt[] = {(unsigned char)cmd, 0x01, 0x01};
            send_raw(h, pkt, 3);
            printf("CMD 0x%02X sent\n", cmd); fflush(stdout);
            usleep(300000);
        }
    } else {
        unsigned char pkt[64] = {0};
        const char *hex = argv[1]; int len = 0;
        while (*hex && len < 64) {
            if (*hex==' '||*hex=='\n'||*hex=='\t'){hex++;continue;}
            unsigned int byte;
            if (sscanf(hex,"%2x",&byte)!=1) break;
            pkt[len++]=(unsigned char)byte; hex+=2;
        }
        if (len == 0) { cleanup(h); libusb_exit(NULL); return 1; }
        r = send_raw(h, pkt, len);
        if (r>=0) {
            printf("[OK] Sent %d bytes:", len);
            for (int i=0; i<len; i++) printf(" %02X", pkt[i]);
            printf("\n");
        } else fprintf(stderr,"Send error: %s\n", libusb_error_name(r));
    }
    usleep(50000);
    cleanup(h);
    libusb_exit(NULL);
    return (r < 0) ? 1 : 0;
}
