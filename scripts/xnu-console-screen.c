/* Draw the kernel console on the frame buffer.
 *
 * Reported by patch-xnu-console-screen.py: __cnputs is redirected here, so this
 * is the single sink for every kernel message (console_write and
 * console_write_char both funnel into it). The board has no display driver, so
 * the kernel's own video console never becomes ready and every kprintf was
 * being dropped inside vcputc_options. DarlingBoot's handoff already leaves a
 * drawable frame buffer and the panic screen has been painting on it for days,
 * so the same mapping is used here for ordinary output.
 *
 * Two things this deliberately does NOT do:
 *
 *   scroll   one line of scroll copies the whole panel (~14 MB at 1116x2480)
 *            and the kernel prints thousands of lines, which would stall the
 *            boot for minutes. The screen is used as a ring instead: once the
 *            bottom is reached the cursor wraps to the top and overwrites the
 *            oldest line. What a crash needs is the newest lines, which is
 *            exactly what that keeps.
 *
 *   globals  this code is placed in the kernel's read-only text, so a writable
 *            global would fault on its first store. The cursor lives in the
 *            last line of the frame buffer itself, which is why the text area
 *            stops one line short of the bottom. Nothing in the kernel is
 *            touched, so no reserved address has to be trusted.
 *
 * Built freestanding, position dependent, no libc.
 */
typedef unsigned long u64;
typedef unsigned int u32;
typedef unsigned char u8;
typedef int s32;

struct vc_info {
  u32 v_height, v_width, v_depth, v_rowbytes;
  u64 v_baseaddr;
};

/* Cursor block, stored at the first pixels of the bottom scan line.
 *
 * Every field is volatile on purpose. XNU runs with SCTLR.A set (A=1 in
 * 0x3454593d), so an unaligned access aborts even on normal memory, and the
 * frame buffer here is device memory on top of that. The first version of this
 * file declared the block as plain u32s and clang helpfully fused
 * "x = 0; y = 0" into a single 64-bit store, which landed at offset 4 of the
 * block and aborted on the very first kprintf. Volatile keeps each field a
 * separate naturally aligned 32-bit access. */
struct cur {
  volatile u32 magic;
  volatile u32 x;   /* pixels */
  volatile u32 y;   /* pixels */
};

#define MAGIC 0x4E585343u  /* 'NXSC' */

#ifndef VINFO_ADDR
#define VINFO_ADDR 0UL
#endif

#define ROWS 12
#define COLS 5

extern const u64 console_font[];

__attribute__((section(".text.a_entry"))) void console_screen (const char *s, s32 len);

static void erase_line (u8 *base, u32 rb, u32 width, u32 y, u32 lh)
{
  u32 r;
  /* Volatile 32-bit stores only. Two neighbouring stores would otherwise be
   * fused into one 64-bit store, and a row start is only guaranteed 4-aligned
   * because rb is only known to be a multiple of 4. */
  for (r = 0; r < lh; r++) {
    volatile u32 *p = (volatile u32 *)(base + (u64)(y + r) * rb);
    u32 x;
    for (x = 0; x < width; x++) {
      p[x] = 0;
    }
  }
}

/* Page flip.
 *
 * Wrapping to the top without clearing made each new page overwrite the old one in place, so a photo of a stalled boot
 * showed a screen whose top half was current and whose bottom half was minutes old. Reading "the last thing it printed"
 * off that screen was guesswork, and it is why a stall at kernel 11 s was read as a stall at 26 s for several boots.
 * Clearing the text area first makes every page self-contained: the last line drawn ends at the cursor, and everything
 * below it is blank. */
static void clear_text (u8 *base, u32 rb, u32 width, u32 text_h, u32 lh, struct cur *g)
{
  u32 y;
  for (y = 0; y + lh <= text_h; y += lh) {
    erase_line (base, rb, width, y, lh);
  }
  g->x = 0;
  g->y = 0;
}

static void draw_glyph (u8 *base, u32 rb, u32 scale, u32 x, u32 y, u32 c)
{
  u64 bits = console_font[c - 32];
  u32 row;

  for (row = 0; row < ROWS; row++) {
    u32 data = (u32)((row < 6) ? ((bits >> 32) >> (row * 5))
                               : (bits >> ((row - 6) * 5))) & 0x1Fu;
    u32 col;
    for (col = 0; col < COLS; col++) {
      u32 dy;
      if (!((data >> col) & 1)) {
        continue;
      }
      for (dy = 0; dy < scale; dy++) {
        volatile u32 *p = (volatile u32 *)(base + (u64)(y + row * scale + dy) * rb);
        u32 dx;
        for (dx = 0; dx < scale; dx++) {
          p[x + col * scale + dx] = 0xFFFFFFFFu;
        }
      }
    }
  }
}

void console_screen (const char *s, s32 len)
{
  struct vc_info *v = (struct vc_info *)VINFO_ADDR;
  u32 width, height, rb, scale, cw, lh, text_h;
  u8 *base;
  struct cur *g;
  s32 i, n;

  base   = (u8 *)v->v_baseaddr;
  width  = v->v_width;
  height = v->v_height;
  rb     = v->v_rowbytes;

  /* 4-byte alignment is not cosmetic here: with SCTLR.A set every access must
   * be naturally aligned, and every store below is 32-bit. */
  if (!base || ((u64)base & 3) || (rb & 3) || v->v_depth != 32 ||
      rb < 4 || width < 64 || height < 64) {
    return;
  }
  /* The original __cnputs returns immediately for len < 1, so the same is done
   * here: there is no NUL-terminated mode to fall back to. */
  if (!s || len < 1 || (u64)s < 0xffff000000000000UL) {
    return;
  }
  if (len > 8192) {
    len = 8192;
  }

  /* Largest scale that still leaves a useful log window. 80 columns keeps
   * kernel lines from wrapping too badly; 32 lines is the minimum worth
   * photographing. */
  scale = 1;
  if (width >= 6u * 2 * 80 && height >= 14u * 2 * 32) {
    scale = 2;
  }
  if (width >= 6u * 3 * 80 && height >= 14u * 3 * 32) {
    scale = 3;
  }
  cw = 6 * scale;
  lh = 14 * scale;
  if (height < lh * 2 + 1) {
    return;
  }
  text_h = height - lh;   /* bottom line is the cursor block */

  g = (struct cur *)(base + (u64)(height - 1) * rb);
  if (g->magic != MAGIC || g->x >= width || g->y >= text_h) {
    g->magic = MAGIC;
    g->x = 0;
    g->y = 0;
  }

  n = len;

  for (i = 0; i < n; i++) {
    u32 c = (u8)s[i];

    if (c == '\n') {      g->x = 0;
      g->y += lh;
      if (g->y + lh > text_h) {
        clear_text (base, rb, width, text_h, lh, g);
      }
      continue;
    }
    if (c == '\r') {
      g->x = 0;
      continue;
    }
    if (c == '\t') {
      g->x = (g->x + 4 * cw) & ~(cw - 1);
      continue;
    }
    if (c < 32 || c > 126) {
      continue;
    }
    if (g->x + cw > width) {
      g->x = 0;
      g->y += lh;
      if (g->y + lh > text_h) {
        clear_text (base, rb, width, text_h, lh, g);
      }
    }
    if (g->x == 0) {
      erase_line (base, rb, width, g->y, lh);
    }
    draw_glyph (base, rb, scale, g->x, g->y, c);
    g->x += cw;
  }
}

/* 5x12 glyphs, same table the UEFI frame buffer console and the panic screen
 * use, so a photo of the kernel log looks like the firmware screens. */
#include "Font.h"
__attribute__((section(".text.z_font"))) const u64 console_font[] = GLYPH_FONT;
