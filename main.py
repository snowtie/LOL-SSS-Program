import os
import json
import time
import threading
import queue
from dataclasses import dataclass

import pygame
import tkinter as tk
from tkinter import filedialog

import cv2
import numpy as np
import requests
from mss import mss
import urllib3

# 로컬 라이브클라(127.0.0.1) + verify=False 경고 끄기
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# =============================
# Pygame init
# =============================
pygame.init()

# ✅ 채널을 명시적으로 쓰려고 pre_init + set_num_channels 권장
pygame.mixer.pre_init(frequency=44100, size=-16, channels=2, buffer=512)
pygame.mixer.init()
pygame.mixer.set_num_channels(8)

# ✅ 등급 배경음악은 mixer.music 사용 (단 1회 재생)
# ✅ 펜타/미리듣기 SFX는 Channel로 재생 (music와 분리)
SFX_CHANNEL = pygame.mixer.Channel(1)

W, H = 1100, 650
screen = pygame.display.set_mode((W, H), pygame.RESIZABLE)
pygame.display.set_caption("Samira Sound Tool (UI + Detection + JSON Presets)")

clock = pygame.time.Clock()

# =============================
# Fonts
# =============================
def load_font(size):
    for name in ["Malgun Gothic", "AppleGothic", "NanumGothic", None]:
        try:
            return pygame.font.SysFont(name, size)
        except:
            pass
    return pygame.font.SysFont(None, size)

FONT_12 = load_font(12)
FONT_14 = load_font(14)
FONT_16 = load_font(16)

# =============================
# Utils
# =============================
def clamp(v, a, b):
    return max(a, min(b, v))

def lerp(a, b, t):
    return a + (b - a) * t

def lerp_color(c1, c2, t):
    return (int(lerp(c1[0], c2[0], t)),
            int(lerp(c1[1], c2[1], t)),
            int(lerp(c1[2], c2[2], t)))

def draw_round_rect(surf, rect, color, radius=10, border=0, border_color=(0, 0, 0)):
    pygame.draw.rect(surf, color, rect, border_radius=radius)
    if border > 0:
        pygame.draw.rect(surf, border_color, rect, width=border, border_radius=radius)

def draw_shadow_card(surf, rect, fill, radius=12, shadow_alpha=90, shadow_offset=(0, 4)):
    shadow = pygame.Surface((rect.w + 20, rect.h + 20), pygame.SRCALPHA)
    srect = pygame.Rect(10 + shadow_offset[0], 10 + shadow_offset[1], rect.w, rect.h)
    pygame.draw.rect(shadow, (0, 0, 0, shadow_alpha), srect, border_radius=radius)
    surf.blit(shadow, (rect.x - 10, rect.y - 10))
    draw_round_rect(surf, rect, fill, radius=radius)

# =============================
# Theme
# =============================
@dataclass
class Theme:
    bg: tuple = (14, 16, 20)
    panel: tuple = (20, 24, 30)
    card: tuple = (26, 30, 38)
    stroke: tuple = (45, 52, 63)
    text: tuple = (235, 238, 245)
    subtext: tuple = (165, 175, 190)
    accent: tuple = (120, 170, 255)
    danger: tuple = (255, 105, 105)

THEME = Theme()

# =============================
# File pickers
# =============================
def pick_audio_file():
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    file_path = filedialog.askopenfilename(
        title="사운드 파일 선택",
        filetypes=[
            ("Audio Files", "*.mp3 *.wav *.ogg"),
            ("MP3", "*.mp3"),
            ("WAV", "*.wav"),
            ("OGG", "*.ogg"),
            ("All Files", "*.*"),
        ],
    )
    root.destroy()
    return file_path

def pick_json_save_path(default_name="tool_config.json"):
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    file_path = filedialog.asksaveasfilename(
        title="설정 JSON 저장",
        defaultextension=".json",
        initialfile=default_name,
        filetypes=[("JSON", "*.json"), ("All Files", "*.*")],
    )
    root.destroy()
    return file_path

def pick_json_open_path(title="설정 JSON 불러오기"):
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    file_path = filedialog.askopenfilename(
        title=title,
        filetypes=[("JSON", "*.json"), ("All Files", "*.*")],
    )
    root.destroy()
    return file_path

def safe_read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("[ERROR] JSON 읽기 실패:", path, e)
        return None

def safe_write_json(path, data):
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print("[SAVE]", path)
        return True
    except Exception as e:
        print("[ERROR] JSON 저장 실패:", path, e)
        return False

# =============================
# Audio helpers
# =============================
def set_music_volume(vol_0_100):
    pygame.mixer.music.set_volume(clamp(vol_0_100, 0, 100) / 100.0)

def stop_music():
    try:
        pygame.mixer.music.stop()
    except:
        pass

# ✅ 등급 배경음악: "한 번만 재생" (네가 준 참고 함수 방식)
current_music_grade = None

def play_music_for_grade(grade, music_path, volume_0_100):
    global current_music_grade

    # None이면 아무 것도 하지 않음 (음악 유지)
    if grade == "None":
        return

    if not music_path or not os.path.exists(music_path):
        return

    # 같은 등급이면 재시작 금지 (음악 끝났어도 재시작 안 함)
    if grade == current_music_grade:
        return

    pygame.mixer.music.stop()
    pygame.mixer.music.load(music_path)
    set_music_volume(volume_0_100)

    # ✅ 한 번만 재생 (반복 X)
    pygame.mixer.music.play(0)

    current_music_grade = grade

# ✅ SFX(펜타/미리듣기): Channel로 재생 (music와 분리)
# ✅ 재생 동안 배경음악 볼륨 1/2 덕킹 -> SFX 종료 후 복귀
_ducking = False
_base_music_volume_0_100 = 30

def play_sfx_one_shot(path, volume_0_100, duck=True):
    global _ducking
    if not path or not os.path.exists(path):
        return

    try:
        snd = pygame.mixer.Sound(path)
    except Exception as e:
        print("[SFX LOAD FAIL]", path, e)
        return

    # SFX 볼륨은 유저 볼륨 그대로 (원하면 여기서도 0.5 적용 가능)
    snd.set_volume(clamp(volume_0_100, 0, 100) / 100.0)

    if duck:
        _ducking = True
        # 배경음악 볼륨 1/2
        set_music_volume(volume_0_100 * 0.5)

    # 기존 SFX 즉시 컷(원치 않으면 stop 제거)
    try:
        SFX_CHANNEL.stop()
    except:
        pass

    SFX_CHANNEL.play(snd)

def update_ducking(volume_0_100):
    """매 프레임 호출: SFX가 끝나면 배경음악 볼륨을 원상복귀"""
    global _ducking
    if _ducking and (not SFX_CHANNEL.get_busy()):
        _ducking = False
        set_music_volume(volume_0_100)

# =============================
# UI base
# =============================
class UIElement:
    def __init__(self, rect):
        self.rect = pygame.Rect(rect)
        self.hover = False
        self.pressed = False
        self.enabled = True

    def set_rect(self, rect):
        self.rect = pygame.Rect(rect)

    def hit_test(self, pos):
        return self.rect.collidepoint(pos)

    def handle_event(self, event):
        pass

    def update(self, dt):
        pass

    def draw(self, surf):
        pass

# =============================
# Button
# =============================
class Button(UIElement):
    def __init__(self, rect, text, on_click=None):
        super().__init__(rect)
        self.text = text
        self.on_click = on_click

        self.base = THEME.card
        self.hover_c = lerp_color(THEME.card, (255, 255, 255), 0.06)
        self.press_c = lerp_color(THEME.card, (0, 0, 0), 0.18)

        self.current_color = self.base
        self.target_color = self.base

    def handle_event(self, event):
        if not self.enabled:
            return

        if event.type == pygame.MOUSEMOTION:
            self.hover = self.hit_test(event.pos)

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.hover:
                self.pressed = True

        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self.pressed and self.hover and self.on_click:
                self.on_click()
            self.pressed = False

    def update(self, dt):
        if not self.enabled:
            self.target_color = lerp_color(self.base, (0, 0, 0), 0.25)
        elif self.pressed:
            self.target_color = self.press_c
        elif self.hover:
            self.target_color = self.hover_c
        else:
            self.target_color = self.base

        self.current_color = lerp_color(self.current_color, self.target_color, 0.25)

    def draw(self, surf):
        draw_shadow_card(surf, self.rect, self.current_color, radius=10, shadow_alpha=70)
        if self.hover and self.enabled:
            draw_round_rect(surf, self.rect, self.current_color, radius=10, border=1, border_color=THEME.stroke)

        label = FONT_14.render(self.text, True, THEME.text)
        surf.blit(label, (self.rect.centerx - label.get_width() // 2,
                          self.rect.centery - label.get_height() // 2))

# =============================
# Slider + clickable value input
# =============================
class Slider(UIElement):
    def __init__(self, rect, label, vmin=0, vmax=100, value=30, on_change=None):
        super().__init__(rect)
        self.label = label
        self.vmin = vmin
        self.vmax = vmax
        self.value = int(value)
        self.on_change = on_change
        self.dragging = False

        self.value_rect = pygame.Rect(0, 0, 1, 1)
        self.editing = False
        self.edit_text = ""

    def handle_event(self, event):
        if not self.enabled:
            return

        if event.type == pygame.MOUSEMOTION:
            self.hover = self.hit_test(event.pos)
            if self.dragging:
                self._set_by_mouse(event.pos[0])

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.value_rect.collidepoint(event.pos):
                self.editing = True
                self.edit_text = str(self.value)
                return

            if self.editing and not self.value_rect.collidepoint(event.pos):
                self._commit_edit()
                self.editing = False

            if self.hover:
                self.dragging = True
                self._set_by_mouse(event.pos[0])

        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self.dragging = False

        if event.type == pygame.KEYDOWN and self.editing:
            if event.key == pygame.K_ESCAPE:
                self.editing = False
                return
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self._commit_edit()
                self.editing = False
                return
            if event.key == pygame.K_BACKSPACE:
                self.edit_text = self.edit_text[:-1]
                return

            if event.unicode.isdigit():
                if len(self.edit_text) < 3:
                    self.edit_text += event.unicode

    def _commit_edit(self):
        if self.edit_text.strip() == "":
            return
        try:
            v = int(self.edit_text)
        except:
            return
        v = int(clamp(v, self.vmin, self.vmax))
        self.value = v
        if self.on_change:
            self.on_change(self.value)

    def _set_by_mouse(self, mx):
        track = pygame.Rect(self.rect.x, self.rect.y + 26, self.rect.w, 6)
        t = (mx - track.x) / track.w
        t = clamp(t, 0.0, 1.0)
        self.value = int(lerp(self.vmin, self.vmax, t))
        if self.on_change:
            self.on_change(self.value)

    def draw(self, surf):
        label = FONT_14.render(self.label, True, THEME.subtext)
        surf.blit(label, (self.rect.x, self.rect.y))

        val_str = self.edit_text if self.editing else f"{self.value}"
        val = FONT_14.render(val_str, True, THEME.text)

        padding = 8
        box_w = max(48, val.get_width() + padding * 2)
        box_h = 24
        self.value_rect = pygame.Rect(self.rect.right - box_w, self.rect.y - 2, box_w, box_h)

        box_color = lerp_color(THEME.card, THEME.accent, 0.18) if self.editing else THEME.card
        draw_round_rect(surf, self.value_rect, box_color, radius=8, border=1, border_color=THEME.stroke)

        surf.blit(val, (self.value_rect.centerx - val.get_width() // 2,
                        self.value_rect.centery - val.get_height() // 2))

        track = pygame.Rect(self.rect.x, self.rect.y + 26, self.rect.w, 6)
        draw_round_rect(surf, track, THEME.stroke, radius=999)

        t = (self.value - self.vmin) / (self.vmax - self.vmin)
        fill_w = int(track.w * t)
        fill_rect = pygame.Rect(track.x, track.y, fill_w, track.h)
        draw_round_rect(surf, fill_rect, THEME.accent, radius=999)

        hx = track.x + fill_w
        pygame.draw.circle(surf, (240, 242, 248), (hx, track.centery), 9)
        pygame.draw.circle(surf, THEME.stroke, (hx, track.centery), 9, 1)

        if self.editing:
            hint = FONT_12.render("0~100 입력 후 Enter", True, THEME.subtext)
            surf.blit(hint, (self.rect.x, self.rect.y + 52))

# =============================
# Select box
# =============================
class Select(UIElement):
    def __init__(self, rect, label, options, on_change=None):
        super().__init__(rect)
        self.label = label
        self.options = options
        self.on_change = on_change
        self.selected = 0
        self.opened = False
        self.option_rects = []
        self.dropdown_rect = None
        self.scroll_y = 0
        self.max_drop_h = 180

    def set_index(self, idx):
        if len(self.options) == 0:
            self.selected = 0
            return
        self.selected = int(clamp(idx, 0, len(self.options) - 1))

    def handle_event(self, event):
        if not self.enabled:
            return

        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.opened = False
            return

        if self.opened and event.type == pygame.MOUSEWHEEL:
            mx, my = pygame.mouse.get_pos()
            if self.dropdown_rect and self.dropdown_rect.collidepoint((mx, my)):
                total_h = len(self.options) * (self.rect.h - 20) + max(0, len(self.options) - 1) * 6
                visible_h = min(total_h, self.max_drop_h)
                max_scroll = max(0, total_h - visible_h)
                self.scroll_y = clamp(self.scroll_y + (-event.y) * 30, 0, max_scroll)
            return

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.opened:
                for i, rect in enumerate(self.option_rects):
                    if rect.collidepoint(event.pos):
                        self.selected = i
                        if self.on_change:
                            self.on_change(i)
                        self.opened = False
                        return
                if not self.rect.collidepoint(event.pos):
                    self.opened = False
                    return

            if self.rect.collidepoint(event.pos):
                self.opened = not self.opened

    def draw(self, surf):
        label = FONT_14.render(self.label, True, THEME.subtext)
        surf.blit(label, (self.rect.x, self.rect.y))

        box_h = self.rect.h - 20
        box_rect = pygame.Rect(self.rect.x, self.rect.y + 18, self.rect.w, box_h)
        draw_round_rect(surf, box_rect, THEME.card, radius=10, border=1, border_color=THEME.stroke)

        sel_txt = self.options[self.selected] if self.options else ""
        val = FONT_14.render(sel_txt, True, THEME.text)
        surf.blit(val, (box_rect.x + 12, box_rect.centery - val.get_height() // 2))

        arrow = FONT_14.render("▼" if not self.opened else "▲", True, THEME.subtext)
        surf.blit(arrow, (box_rect.right - arrow.get_width() - 12, box_rect.centery - arrow.get_height() // 2))

        self.option_rects = []
        if self.opened and self.options:
            opt_y = box_rect.bottom + 6
            total_h = len(self.options) * box_h + (len(self.options) - 1) * 6
            view_h = min(total_h, self.max_drop_h)
            self.scroll_y = clamp(self.scroll_y, 0, max(0, total_h - view_h))

            self.dropdown_rect = pygame.Rect(box_rect.x, opt_y, box_rect.w, view_h)
            draw_round_rect(surf, self.dropdown_rect, THEME.panel, radius=10, border=1, border_color=THEME.stroke)

            prev_clip = surf.get_clip()
            surf.set_clip(self.dropdown_rect)

            for i, opt in enumerate(self.options):
                orect = pygame.Rect(box_rect.x, opt_y - int(self.scroll_y), box_rect.w, box_h)
                bg = lerp_color(THEME.card, THEME.accent, 0.12) if i == self.selected else THEME.card
                draw_round_rect(surf, orect, bg, radius=8, border=0, border_color=THEME.stroke)
                t = FONT_14.render(opt, True, THEME.text)
                surf.blit(t, (orect.x + 12, orect.centery - t.get_height() // 2))
                self.option_rects.append(orect)
                opt_y += box_h + 6

            surf.set_clip(prev_clip)

# =============================
# SoundSlotList
# =============================
class SoundSlotList(UIElement):
    def __init__(self, rect, get_volume_func, on_play_click=None):
        super().__init__(rect)
        self.get_volume = get_volume_func
        self.on_play_click = on_play_click

        self.scroll_y = 0
        self.scroll_target = 0
        self.slots = []
        self._hit_play = []
        self._hit_pick = []
        self.header_title = "SOUND SLOTS"

    def set_slots(self, slots, header_title="SOUND SLOTS"):
        self.slots = slots
        self.header_title = header_title
        self.scroll_y = 0
        self.scroll_target = 0

    def handle_event(self, event):
        if not self.enabled:
            return

        if event.type == pygame.MOUSEWHEEL:
            mx, my = pygame.mouse.get_pos()
            if self.rect.collidepoint((mx, my)):
                self.scroll_target += (-event.y) * 60
                self._clamp_scroll()

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if not self.rect.collidepoint(event.pos):
                return

            for i in range(len(self.slots)):
                # ▶ 미리듣기: SFX 채널로 재생 + 덕킹
                if i < len(self._hit_play) and self._hit_play[i].collidepoint(event.pos):
                    slot = self.slots[i]
                    if self.on_play_click:
                        self.on_play_click(slot)
                    else:
                        path = slot.get("path", "")
                        play_sfx_one_shot(path, self.get_volume(), duck=True)
                    return

                if i < len(self._hit_pick) and self._hit_pick[i].collidepoint(event.pos):
                    picked = pick_audio_file()
                    if picked:
                        self.slots[i]["path"] = picked
                        print("[SET]", self.slots[i]["title"], "=>", picked)
                    return

    def update(self, dt):
        self.scroll_y += (self.scroll_target - self.scroll_y) * 0.25
        self._clamp_scroll()

    def _clamp_scroll(self):
        content_h = self._content_height()
        view_h = self.rect.h - 70
        max_scroll = max(0, content_h - view_h)
        self.scroll_target = clamp(self.scroll_target, 0, max_scroll)
        self.scroll_y = clamp(self.scroll_y, 0, max_scroll)

    def _content_height(self):
        return len(self.slots) * 74

    def draw(self, surf):
        draw_shadow_card(surf, self.rect, THEME.panel, radius=16, shadow_alpha=90)

        title = FONT_16.render(self.header_title, True, THEME.text)
        surf.blit(title, (self.rect.x + 16, self.rect.y + 16))

        hint = FONT_12.render("휠로 스크롤 / ▶ 미리듣기(SFX) / … 파일 지정", True, THEME.subtext)
        surf.blit(hint, (self.rect.x + 16, self.rect.y + 40))

        list_area = pygame.Rect(self.rect.x + 16, self.rect.y + 70, self.rect.w - 32, self.rect.h - 86)

        prev_clip = surf.get_clip()
        surf.set_clip(list_area)

        self._hit_play = []
        self._hit_pick = []

        y = list_area.y - int(self.scroll_y)
        for slot in self.slots:
            card = pygame.Rect(list_area.x, y, list_area.w, 64)
            draw_round_rect(surf, card, THEME.card, radius=12, border=1, border_color=THEME.stroke)

            title_txt = FONT_14.render(slot["title"], True, THEME.text)
            surf.blit(title_txt, (card.x + 12, card.y + 10))

            path = slot.get("path", "")
            if not path:
                path_disp = "(미지정)"
                path_color = lerp_color(THEME.subtext, THEME.card, 0.25)
            else:
                path_disp = path
                if len(path_disp) > 60:
                    path_disp = "..." + path_disp[-57:]
                path_color = THEME.subtext

            path_txt = FONT_12.render(path_disp, True, path_color)
            surf.blit(path_txt, (card.x + 12, card.y + 34))

            btn_w = 44
            gap = 10
            btn_play = pygame.Rect(card.right - (btn_w * 2 + gap) - 12, card.y + 14, btn_w, 36)
            btn_pick = pygame.Rect(card.right - btn_w - 12, card.y + 14, btn_w, 36)

            draw_round_rect(surf, btn_play, lerp_color(THEME.card, THEME.accent, 0.25), radius=10, border=1, border_color=THEME.stroke)
            draw_round_rect(surf, btn_pick, THEME.card, radius=10, border=1, border_color=THEME.stroke)

            t1 = FONT_14.render("▶", True, THEME.text)
            surf.blit(t1, (btn_play.centerx - t1.get_width() // 2, btn_play.centery - t1.get_height() // 2))

            t2 = FONT_14.render("…", True, THEME.text)
            surf.blit(t2, (btn_pick.centerx - t2.get_width() // 2, btn_pick.centery - t2.get_height() // 2))

            self._hit_play.append(btn_play)
            self._hit_pick.append(btn_pick)

            y += 74

        surf.set_clip(prev_clip)

# =============================
# PresetList
# =============================
class PresetList(UIElement):
    def __init__(self, rect, apply_preset_func):
        super().__init__(rect)
        self.apply_preset = apply_preset_func
        self.scroll_y = 0
        self.scroll_target = 0
        self.items = []
        self._hit_items = []

    def reload(self):
        os.makedirs("presets", exist_ok=True)
        files = [fn for fn in os.listdir("presets") if fn.lower().endswith(".json")]
        files.sort()
        self.items = [{"name": os.path.splitext(fn)[0], "path": os.path.join("presets", fn)} for fn in files]
        self.scroll_y = 0
        self.scroll_target = 0
        print("[PRESETS] found:", len(self.items))

    def handle_event(self, event):
        if not self.enabled:
            return

        if event.type == pygame.MOUSEWHEEL:
            mx, my = pygame.mouse.get_pos()
            if self.rect.collidepoint((mx, my)):
                self.scroll_target += (-event.y) * 60
                self._clamp_scroll()

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if not self.rect.collidepoint(event.pos):
                return
            for i in range(len(self._hit_items)):
                if self._hit_items[i].collidepoint(event.pos):
                    item = self.items[i]
                    data = safe_read_json(item["path"])
                    if data:
                        self.apply_preset(data, preset_name=item["name"])
                    return

    def update(self, dt):
        self.scroll_y += (self.scroll_target - self.scroll_y) * 0.25
        self._clamp_scroll()

    def _content_height(self):
        return len(self.items) * 74

    def _clamp_scroll(self):
        content_h = self._content_height()
        view_h = self.rect.h - 70
        max_scroll = max(0, content_h - view_h)
        self.scroll_target = clamp(self.scroll_target, 0, max_scroll)
        self.scroll_y = clamp(self.scroll_y, 0, max_scroll)

    def draw(self, surf):
        draw_shadow_card(surf, self.rect, THEME.panel, radius=16, shadow_alpha=90)

        title = FONT_16.render("PRESETS", True, THEME.text)
        surf.blit(title, (self.rect.x + 16, self.rect.y + 16))

        hint = FONT_12.render("presets/*.json 목록 / 클릭하면 적용 / 휠 스크롤", True, THEME.subtext)
        surf.blit(hint, (self.rect.x + 16, self.rect.y + 40))

        list_area = pygame.Rect(self.rect.x + 16, self.rect.y + 70, self.rect.w - 32, self.rect.h - 86)
        prev_clip = surf.get_clip()
        surf.set_clip(list_area)

        self._hit_items = []
        y = list_area.y - int(self.scroll_y)

        if not self.items:
            empty = FONT_14.render("presets 폴더에 JSON이 없습니다.", True, THEME.subtext)
            surf.blit(empty, (list_area.x, list_area.y))
        else:
            for item in self.items:
                card = pygame.Rect(list_area.x, y, list_area.w, 64)
                draw_round_rect(surf, card, THEME.card, radius=12, border=1, border_color=THEME.stroke)

                name_txt = FONT_14.render(item["name"], True, THEME.text)
                surf.blit(name_txt, (card.x + 12, card.y + 12))

                path_txt = FONT_12.render(item["path"], True, THEME.subtext)
                surf.blit(path_txt, (card.x + 12, card.y + 36))

                self._hit_items.append(card)
                y += 74

        surf.set_clip(prev_clip)

# =============================
# Detection thread (OpenCV + MSS)
# =============================
GRADE_ORDER = ["None", "E", "D", "C", "B", "A", "S"]
GRADE_TO_IDX = {g: i for i, g in enumerate(GRADE_ORDER)}

def idx_grade(g):
    return GRADE_TO_IDX.get(g, 0)

TEMPLATES = {
    "S": [r"templates/S.png", r"templates/S(active).png"],
    "A": [r"templates/A.png"],
    "B": [r"templates/B.png"],
    "C": [r"templates/C.png"],
    "D": [r"templates/D.png"],
    "E": [r"templates/E.png"],
    "None": [r"templates/None.png", r"templates/None(cooltime).png"],
}

anchor_presets = [
    {"label": "1024 x 768",  "anchor": (512, 712),   "resolution": (1024, 768)},
    {"label": "1152 x 864",  "anchor": (576, 801),   "resolution": (1152, 864)},
    {"label": "1280 x 720",  "anchor": (640, 668),   "resolution": (1280, 720)},
    {"label": "1280 x 768",  "anchor": (640, 712),   "resolution": (1280, 768)},
    {"label": "1280 x 800",  "anchor": (640, 742),   "resolution": (1280, 800)},
    {"label": "1280 x 960",  "anchor": (640, 890),   "resolution": (1280, 960)},
    {"label": "1280 x 1024", "anchor": (640, 949),   "resolution": (1280, 1024)},
    {"label": "1360 x 768",  "anchor": (680, 712),   "resolution": (1360, 768)},
    {"label": "1366 x 768",  "anchor": (683, 712),   "resolution": (1366, 768)},
    {"label": "1440 x 900",  "anchor": (720, 834),   "resolution": (1440, 900)},
    {"label": "1440 x 1080", "anchor": (720, 1001),  "resolution": (1440, 1080)},
    {"label": "1600 x 900",  "anchor": (800, 834),   "resolution": (1600, 900)},
    {"label": "1600 x 1024", "anchor": (800, 949),   "resolution": (1600, 1024)},
    {"label": "1600 x 1200", "anchor": (800, 1112),  "resolution": (1600, 1200)},
    {"label": "1680 x 1050", "anchor": (840, 973),   "resolution": (1680, 1050)},
    {"label": "1920 x 1080", "anchor": (960, 1001),  "resolution": (1920, 1080)},
    {"label": "1920 x 1200", "anchor": (960, 1112),  "resolution": (1920, 1200)},
    {"label": "1920 x 1440", "anchor": (960, 1335),  "resolution": (1920, 1440)},
    {"label": "2048 x 1536", "anchor": (1024, 1424), "resolution": (2048, 1536)},
    {"label": "2560 x 1440", "anchor": (1280, 1335), "resolution": (2560, 1440)},
    {"label": "2560 x 1600", "anchor": (1280, 1483), "resolution": (2560, 1600)},
    {"label": "3440 x 1440", "anchor": (1720, 1335), "resolution": (3440, 1440)},
    {"label": "3840 x 2160", "anchor": (1920, 2002), "resolution": (3840, 2160)},
]

anchor_x, anchor_y = anchor_presets[0]["anchor"]
anchor_select = None
ROI_W_BASE, ROI_H_BASE = 90, 90
ROI_W, ROI_H = ROI_W_BASE, ROI_H_BASE
TEMPLATE_BASE_RESOLUTION = (3440, 1440)
TEMPLATE_SCALE_OFFSETS = [0.97, 1.0, 1.03]

def compute_monitor(ax, ay):
    rx = int(ax - ROI_W // 2)
    ry = int(ay - ROI_H // 2)
    return {"left": rx, "top": ry, "width": ROI_W, "height": ROI_H}

monitor = compute_monitor(anchor_x, anchor_y)

tmpl_imgs_base = {g: [] for g in TEMPLATES.keys()}
for grade, paths in TEMPLATES.items():
    for path in paths:
        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise FileNotFoundError(f"템플릿 로드 실패: {grade} -> {path}")
        tmpl_imgs_base[grade].append(img)

tmpl_imgs = {}


def _scale_candidates(base_scale):
    seen = set()
    for off in TEMPLATE_SCALE_OFFSETS:
        candidate = base_scale * off
        key = round(candidate, 4)
        if key in seen:
            continue
        seen.add(key)
        yield candidate


def rebuild_templates(base_scale):
    global tmpl_imgs
    new_tmpls = {}
    for grade, tmpls in tmpl_imgs_base.items():
        scaled_list = []
        for tmpl in tmpls:
            for scale_factor in _scale_candidates(base_scale):
                if scale_factor != 1.0:
                    new_w = max(1, int(round(tmpl.shape[1] * scale_factor)))
                    new_h = max(1, int(round(tmpl.shape[0] * scale_factor)))
                    scaled = cv2.resize(tmpl, (new_w, new_h), interpolation=cv2.INTER_AREA)
                else:
                    scaled = tmpl
                scaled_list.append(scaled)
        new_tmpls[grade] = scaled_list

    tmpl_imgs = new_tmpls


rebuild_templates(1.0)


def resolution_scale(resolution):
    try:
        rw, rh = resolution
        bw, bh = TEMPLATE_BASE_RESOLUTION
        if bh > 0:
            # UI 스케일을 높이 기준으로 정렬 (기본 해상도 3440x1440)
            return max(0.2, rh / bh)
    except Exception:
        pass
    return 1.0

def detect_grade_fn(roi_gray):
    best_grade = None
    best_score = -1.0
    for grade, tmpls in tmpl_imgs.items():
        for tmpl in tmpls:
            if roi_gray.shape[0] < tmpl.shape[0] or roi_gray.shape[1] < tmpl.shape[1]:
                continue
            res = cv2.matchTemplate(roi_gray, tmpl, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(res)
            if max_val > best_score:
                best_score = max_val
                best_grade = grade
    return best_grade, best_score

LIVE_URL = "https://127.0.0.1:2999/liveclientdata/allgamedata"

def is_active_player_samira(timeout_sec=0.2):
    try:
        data = requests.get(LIVE_URL, verify=False, timeout=timeout_sec).json()

        active_name = data.get("activePlayer", {}).get("summonerName", None)
        if not active_name:
            return False

        for p in data.get("allPlayers", []):
            if p.get("summonerName") != active_name:
                continue

            champ = p.get("championName", "")
            raw = p.get("rawChampionName", "")

            if isinstance(raw, str) and ("Samira" in raw):
                return True

            if champ in ("Samira", "사미라"):
                return True

            return False

        return False
    except:
        return False

def get_active_summoner_name(timeout_sec=0.2):
    """펜타 이벤트에서 '내가 한 킬인지' 판별용"""
    try:
        data = requests.get(LIVE_URL, verify=False, timeout=timeout_sec).json()
        return data.get("activePlayer", {}).get("summonerName", None)
    except:
        return None

class DetectionController:
    def __init__(self):
        self.running = True
        self.debug_window = True
        self.lock = threading.Lock()
        self.monitor = monitor

det_ctl = DetectionController()

event_q = queue.Queue()

def detection_thread_main():
    score_threshold = 0.55
    confirm_frames = 3
    none_exit_extra_confirm = 6
    step_interval_sec = 0.05
    drop_confirm_frames = 10
    ramp_hold_sec = 0.1

    last_stable_grade = "None"
    last_step_time = 0.0

    candidate_grade = None
    candidate_count = 0

    ramp_target = None
    ramp_target_idx = None
    ramp_last_seen_time = 0.0

    drop_candidate = None
    drop_count = 0

    s_enter_time = None
    S_TO_NONE_GUARD_SEC = 6.0

    # ✅ 감지 스레드 내에서 "현재 등급 이벤트 중복 방지용"
    current_sent_grade = None

    last_event_id = -1
    penta_played = False

    samira_active = False
    last_samira_poll = 0.0
    SAMIRA_POLL_INTERVAL = 0.35

    sct = mss()

    win_name = "ROI Debug Preview"
    window_created = False

    def ensure_window():
        nonlocal window_created
        if window_created:
            return
        cv2.namedWindow(win_name, cv2.WINDOW_NORMAL)
        cv2.setWindowProperty(win_name, cv2.WND_PROP_TOPMOST, 1)
        window_created = True

    def destroy_window():
        nonlocal window_created
        if window_created:
            try:
                cv2.destroyWindow(win_name)
            except:
                pass
            window_created = False

    def poll_pentakill():
        """
        ✅ 펜타 이벤트는 '내가 킬한 것'만 재생:
        Multikill 이벤트에서 KillerName(또는 유사 필드)이 activePlayer와 일치할 때만 큐에 넣음
        """
        nonlocal last_event_id, penta_played
        if penta_played:
            return

        try:
            data = requests.get(LIVE_URL, verify=False, timeout=0.2).json()
            active_name = data.get("activePlayer", {}).get("summonerName", None)

            if not active_name:
                return

            for e in data.get("events", {}).get("Events", []):
                eid = e.get("EventID", -1)
                if eid <= last_event_id:
                    continue
                last_event_id = max(last_event_id, eid)

                if e.get("EventName") == "Multikill" and e.get("KillStreak") == 5:
                    killer = e.get("KillerName", None)  # 보통 이 키로 들어옴
                    # 혹시 키가 다르면 보조로 몇 개 더 시도 (환경마다 다를 수 있음)
                    if killer is None:
                        killer = e.get("Killer", None) or e.get("PlayerName", None)

                    # ✅ 내 닉이면 재생, 타인이면 무시
                    if killer == active_name:
                        penta_played = True
                        event_q.put(("PENTA", None))
                    else:
                        # 타인이 펜타한 것 -> 재생 X
                        pass

        except:
            pass

    def reset_detection_state():
        nonlocal last_stable_grade, last_step_time, candidate_grade, candidate_count
        nonlocal ramp_target, ramp_target_idx, ramp_last_seen_time
        nonlocal drop_candidate, drop_count
        nonlocal s_enter_time
        nonlocal current_sent_grade

        last_stable_grade = "None"
        last_step_time = 0.0
        candidate_grade = None
        candidate_count = 0
        ramp_target = None
        ramp_target_idx = None
        ramp_last_seen_time = 0.0
        drop_candidate = None
        drop_count = 0
        s_enter_time = None
        current_sent_grade = None

    while True:
        with det_ctl.lock:
            if not det_ctl.running:
                break
            dbg_on = det_ctl.debug_window
            monitor_local = det_ctl.monitor

        now = time.time()

        if (now - last_samira_poll) >= SAMIRA_POLL_INTERVAL:
            last_samira_poll = now
            new_active = is_active_player_samira(timeout_sec=0.2)

            if new_active != samira_active:
                samira_active = new_active
                if not samira_active:
                    reset_detection_state()
                    event_q.put(("SAMIRA_ACTIVE", False))
                else:
                    event_q.put(("SAMIRA_ACTIVE", True))

        if not samira_active:
            if dbg_on:
                ensure_window()
                img = np.zeros((ROI_H * 3, ROI_W * 3, 3), dtype=np.uint8)
                cv2.putText(img, "WAITING: Samira not active", (10, 50),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
                cv2.putText(img, "Detection paused (no screen grab)", (10, 85),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (180, 180, 180), 2, cv2.LINE_AA)
                cv2.imshow(win_name, img)
                cv2.waitKey(1)
            else:
                destroy_window()

            time.sleep(0.05)
            continue

        frame = np.array(sct.grab(monitor_local))
        frame_bgr = frame[:, :, :3]
        roi_gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

        raw_grade, raw_score = detect_grade_fn(roi_gray)
        if raw_grade is None or raw_score < score_threshold:
            raw_grade = "None"

        if raw_grade == candidate_grade:
            candidate_count += 1
        else:
            candidate_grade = raw_grade
            candidate_count = 1

        info_lines = [
            f"SamiraActive=TRUE",
            f"raw={raw_grade} score={raw_score:.3f}",
            f"cand={candidate_grade} ({candidate_count}/{confirm_frames})",
            f"stable={last_stable_grade}",
        ]

        if candidate_count >= confirm_frames:
            proposed = candidate_grade
            stable_i = idx_grade(last_stable_grade)
            proposed_i = idx_grade(proposed)

            if last_stable_grade == "None" and proposed != "None":
                needed = confirm_frames + none_exit_extra_confirm
                info_lines.append(f"NoneExit: need {needed} frames")
                if candidate_count < needed:
                    proposed = "None"
                    proposed_i = idx_grade(proposed)
                else:
                    proposed = "E"
                    proposed_i = idx_grade(proposed)

            if proposed_i > stable_i:
                if ramp_target is None or proposed_i > ramp_target_idx:
                    ramp_target = proposed
                    ramp_target_idx = proposed_i
                ramp_last_seen_time = now

                if ramp_target is not None and (now - ramp_last_seen_time) > ramp_hold_sec:
                    ramp_target = None
                    ramp_target_idx = None

                if ramp_target is not None and (now - last_step_time) >= step_interval_sec:
                    next_i = min(stable_i + 1, ramp_target_idx)
                    if next_i != stable_i:
                        prev = last_stable_grade
                        last_stable_grade = GRADE_ORDER[next_i]
                        last_step_time = now

                        # ✅ 이벤트는 중복 전송 방지 (같은 등급 계속 보내면 재생이 꼬일 수 있음)
                        if last_stable_grade != "None" and last_stable_grade != current_sent_grade:
                            current_sent_grade = last_stable_grade
                            event_q.put(("GRADE", current_sent_grade))

                        if last_stable_grade == "S" and prev != "S":
                            s_enter_time = now

                info_lines.append(f"RAMP target={ramp_target} -> stable={last_stable_grade}")

            elif proposed_i < stable_i:
                dist_down = stable_i - proposed_i
                ramp_target = None
                ramp_target_idx = None

                if last_stable_grade == "S" and proposed == "None":
                    if s_enter_time is None:
                        s_enter_time = now
                    remain = S_TO_NONE_GUARD_SEC - (now - s_enter_time)
                    if remain > 0:
                        info_lines.append(f"S->None blocked ({remain:.1f}s left)")
                        proposed = "S"
                        proposed_i = idx_grade(proposed)
                        dist_down = 0

                if proposed_i < stable_i:
                    if dist_down >= 2:
                        if proposed == drop_candidate:
                            drop_count += 1
                        else:
                            drop_candidate = proposed
                            drop_count = 1

                        info_lines.append(f"DROP? {drop_candidate} ({drop_count}/{drop_confirm_frames}) dist={dist_down}")

                        if drop_count >= drop_confirm_frames:
                            last_stable_grade = proposed
                            last_step_time = now
                            drop_candidate = None
                            drop_count = 0

                            if last_stable_grade != "None" and last_stable_grade != current_sent_grade:
                                current_sent_grade = last_stable_grade
                                event_q.put(("GRADE", current_sent_grade))

                            if last_stable_grade != "S":
                                s_enter_time = None
                    else:
                        last_stable_grade = proposed
                        last_step_time = now
                        drop_candidate = None
                        drop_count = 0

                        if last_stable_grade != "None" and last_stable_grade != current_sent_grade:
                            current_sent_grade = last_stable_grade
                            event_q.put(("GRADE", current_sent_grade))

                        if last_stable_grade != "S":
                            s_enter_time = None
                else:
                    drop_candidate = None
                    drop_count = 0
            else:
                drop_candidate = None
                drop_count = 0

        poll_pentakill()

        if dbg_on:
            ensure_window()
            debug = cv2.resize(frame_bgr, (ROI_W * 3, ROI_H * 3), interpolation=cv2.INTER_NEAREST)
            y = 28
            for line in info_lines:
                cv2.putText(debug, line, (10, y),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2, cv2.LINE_AA)
                y += 26
            cv2.imshow(win_name, debug)
            cv2.waitKey(1)
        else:
            destroy_window()

        time.sleep(0.02)

    try:
        cv2.destroyAllWindows()
    except:
        pass

# =============================
# State + config
# =============================
state = {
    "volume": 30,
    "mode": None,            # None / "samira" / "penta" / "preset"
    "last_preset": None,
    "debug_window": True,
    "samira_active": False,
    "anchor_index": 0,
}

def set_volume(v):
    global _base_music_volume_0_100
    state["volume"] = int(clamp(v, 0, 100))
    _base_music_volume_0_100 = state["volume"]
    # 덕킹 중이면 반영하지 않고, 덕킹 해제 시 복귀
    if not _ducking:
        set_music_volume(state["volume"])

def set_anchor_index(idx, update_ui=True):
    global anchor_x, anchor_y, monitor, ROI_W, ROI_H

    if len(anchor_presets) == 0:
        return

    idx = int(clamp(idx, 0, len(anchor_presets) - 1))
    state["anchor_index"] = idx
    preset = anchor_presets[idx]

    anchor_x, anchor_y = preset["anchor"]

    scale = resolution_scale(preset.get("resolution", TEMPLATE_BASE_RESOLUTION))
    ROI_W = max(20, int(round(ROI_W_BASE * scale)))
    ROI_H = max(20, int(round(ROI_H_BASE * scale)))

    monitor = compute_monitor(anchor_x, anchor_y)

    rebuild_templates(scale)

    with det_ctl.lock:
        det_ctl.monitor = monitor

    if update_ui and anchor_select is not None:
        anchor_select.set_index(idx)

samira_slots = [
    {"title": "S", "path": ""},
    {"title": "A", "path": ""},
    {"title": "B", "path": ""},
    {"title": "C", "path": ""},
    {"title": "D", "path": ""},
    {"title": "E", "path": ""},
]
penta_slots = [
    {"title": "Pentakill", "path": ""},
]

def export_tool_config():
    return {
        "version": 2,
        "volume": state["volume"],
        "debug_window": state["debug_window"],
        "anchor_index": state["anchor_index"],
        "samira": [{"title": s["title"], "path": s.get("path", "")} for s in samira_slots],
        "penta": [{"title": s["title"], "path": s.get("path", "")} for s in penta_slots],
    }

def apply_tool_config(data):
    if not isinstance(data, dict):
        return

    v = data.get("volume", state["volume"])
    if isinstance(v, (int, float)):
        v = int(clamp(v, 0, 100))
        sld_volume.value = v
        set_volume(v)

    dbg = data.get("debug_window", state["debug_window"])
    if isinstance(dbg, bool):
        state["debug_window"] = dbg
        with det_ctl.lock:
            det_ctl.debug_window = dbg

    ai = data.get("anchor_index", state["anchor_index"])
    if isinstance(ai, (int, float)):
        set_anchor_index(int(ai), update_ui=False)
        if anchor_select is not None:
            anchor_select.set_index(state["anchor_index"])

    s_list = data.get("samira", [])
    if isinstance(s_list, list) and len(s_list) > 0:
        for i in range(min(len(samira_slots), len(s_list))):
            path = s_list[i].get("path", "")
            if isinstance(path, str):
                samira_slots[i]["path"] = path

    p_list = data.get("penta", [])
    if isinstance(p_list, list) and len(p_list) > 0:
        for i in range(min(len(penta_slots), len(p_list))):
            path = p_list[i].get("path", "")
            if isinstance(path, str):
                penta_slots[i]["path"] = path

    if state["mode"] == "samira":
        slot_list.set_slots(samira_slots, header_title="SAMIRA (S~E)")
    elif state["mode"] == "penta":
        slot_list.set_slots(penta_slots, header_title="PENTAKILL")

def apply_preset_data(data, preset_name=None):
    apply_tool_config(data)
    state["last_preset"] = preset_name
    print("[APPLY PRESET]", preset_name)

# =============================
# Layout
# =============================
def build_layout(w, h):
    margin = 20
    bottom_h = 150
    sidebar_w = 280

    top_area = pygame.Rect(margin, margin, w - margin * 2, h - margin * 2 - bottom_h)

    canvas_rect = pygame.Rect(
        top_area.x,
        top_area.y,
        top_area.w - sidebar_w - 16,
        top_area.h
    )

    sidebar_rect = pygame.Rect(
        canvas_rect.right + 16,
        top_area.y,
        sidebar_w,
        top_area.h
    )

    bottom_rect = pygame.Rect(
        margin,
        top_area.bottom + 16,
        w - margin * 2,
        bottom_h - 16
    )

    btn_samira = pygame.Rect(sidebar_rect.x + 20, sidebar_rect.y + 70, sidebar_rect.w - 40, 46)
    btn_penta  = pygame.Rect(sidebar_rect.x + 20, sidebar_rect.y + 130, sidebar_rect.w - 40, 46)

    btn_h = 42
    gap = 10
    bx = sidebar_rect.x + 20
    bw = sidebar_rect.w - 40
    by_bottom = sidebar_rect.bottom - 20

    btn_load = pygame.Rect(bx, by_bottom - btn_h, bw, btn_h)
    btn_save = pygame.Rect(bx, btn_load.y - gap - btn_h, bw, btn_h)
    btn_presets = pygame.Rect(bx, btn_save.y - gap - btn_h, bw, btn_h)
    btn_dbg = pygame.Rect(bx, btn_presets.y - gap - btn_h, bw, btn_h)

    sel_anchor = pygame.Rect(bottom_rect.x + 20, bottom_rect.y + 16, 260, 46)
    sld = pygame.Rect(bottom_rect.x + 20, sel_anchor.bottom + 12, bottom_rect.w - 40, 50)

    return canvas_rect, sidebar_rect, bottom_rect, btn_samira, btn_penta, btn_dbg, btn_presets, btn_save, btn_load, sel_anchor, sld

# =============================
# UI create
# =============================
canvas_rect, sidebar_rect, bottom_rect, r_samira, r_penta, r_dbg, r_presets, r_save, r_load, r_anchor, r_sld = build_layout(W, H)

def on_slot_play(slot):
    # ✅ 미리듣기: SFX 채널 + 덕킹
    play_sfx_one_shot(slot.get("path", ""), state["volume"], duck=True)

slot_list = SoundSlotList(canvas_rect, get_volume_func=lambda: state["volume"], on_play_click=on_slot_play)
preset_list = PresetList(canvas_rect, apply_preset_func=apply_preset_data)

def open_samira():
    state["mode"] = "samira"
    slot_list.set_slots(samira_slots, header_title="SAMIRA (S~E)")

def open_penta():
    state["mode"] = "penta"
    slot_list.set_slots(penta_slots, header_title="PENTAKILL")

def open_presets():
    state["mode"] = "preset"
    preset_list.reload()

def save_tool_json():
    path = pick_json_save_path(default_name="tool_config.json")
    if not path:
        return
    safe_write_json(path, export_tool_config())

def load_tool_json():
    path = pick_json_open_path(title="툴 설정 JSON 불러오기")
    if not path:
        return
    data = safe_read_json(path)
    if data:
        apply_tool_config(data)
        state["last_preset"] = None
        print("[LOAD CONFIG]", path)

def toggle_debug_window():
    state["debug_window"] = not state["debug_window"]
    with det_ctl.lock:
        det_ctl.debug_window = state["debug_window"]

btn_open_samira = Button(r_samira, "사미라 스타일 사운드(S ~ E)", on_click=open_samira)
btn_open_penta  = Button(r_penta,  "펜타킬 사운드", on_click=open_penta)
btn_debug       = Button(r_dbg,    "감지 창 ON/OFF", on_click=toggle_debug_window)
btn_open_presets= Button(r_presets,"프리셋", on_click=open_presets)
btn_save        = Button(r_save,   "저장", on_click=save_tool_json)
btn_load        = Button(r_load,   "불러오기", on_click=load_tool_json)

def on_anchor_changed(idx):
    set_anchor_index(idx, update_ui=False)

anchor_select = Select(r_anchor, "앵커(해상도)", [p["label"] for p in anchor_presets], on_change=on_anchor_changed)
anchor_select.set_index(state["anchor_index"])
set_anchor_index(state["anchor_index"], update_ui=False)
sld_volume = Slider(r_sld, "Volume", 0, 100, state["volume"], on_change=set_volume)
ui = [btn_open_samira, btn_open_penta, btn_debug, btn_open_presets, btn_save, btn_load, anchor_select, sld_volume]

# 시작 볼륨 적용
set_music_volume(state["volume"])

# =============================
# Start detection thread
# =============================
t = threading.Thread(target=detection_thread_main, daemon=True)
t.start()

# =============================
# Grade -> sound mapping (UI slots: S~E)
# =============================
grade_to_slot_index = {
    "S": 0,
    "A": 1,
    "B": 2,
    "C": 3,
    "D": 4,
    "E": 5
}

def handle_detection_events():
    global current_music_grade
    while True:
        try:
            typ, payload = event_q.get_nowait()
        except queue.Empty:
            break

        if typ == "SAMIRA_ACTIVE":
            state["samira_active"] = bool(payload)
            print("[SAMIRA_ACTIVE]", state["samira_active"])

            if not state["samira_active"]:
                # 사미라가 아니면 배경음악도 끔
                stop_music()
                current_music_grade = None

        elif typ == "GRADE":
            g = payload
            print("[GRADE EVENT]", g, "current_music=", current_music_grade, "samira_active=", state["samira_active"])

            if not state["samira_active"]:
                continue

            if g == "None":
                # None은 "아무 것도 안함(음악 유지)" 원칙
                continue

            idxi = grade_to_slot_index.get(g, None)
            if idxi is None:
                continue

            path = samira_slots[idxi].get("path", "")
            if not (path and os.path.exists(path)):
                print("[WARN] sound path missing:", g, path)
                continue

            # ✅ 등급 배경음악: 한 번만 재생 (반복 X), 같은 등급이면 재시작 X
            play_music_for_grade(g, path, state["volume"])

        elif typ == "PENTA":
            print("[PENTA EVENT] samira_active=", state["samira_active"])

            if not state["samira_active"]:
                continue

            path = penta_slots[0].get("path", "")
            if path and os.path.exists(path):
                # ✅ 펜타는 SFX 채널로 + 덕킹
                play_sfx_one_shot(path, state["volume"], duck=True)
            else:
                print("[WARN] penta path missing:", path)

# =============================
# Main loop
# =============================
running = True
while running:
    dt = clock.tick(60) / 1000.0

    # 감지 이벤트 처리(메인에서 오디오 실행)
    handle_detection_events()

    # ✅ SFX가 끝났는지 체크해서 덕킹 자동 복귀
    update_ducking(state["volume"])

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False

        if event.type == pygame.VIDEORESIZE:
            W, H = event.w, event.h
            screen = pygame.display.set_mode((W, H), pygame.RESIZABLE)

            canvas_rect, sidebar_rect, bottom_rect, r_samira, r_penta, r_dbg, r_presets, r_save, r_load, r_anchor, r_sld = build_layout(W, H)

            btn_open_samira.set_rect(r_samira)
            btn_open_penta.set_rect(r_penta)
            btn_debug.set_rect(r_dbg)
            btn_open_presets.set_rect(r_presets)
            btn_save.set_rect(r_save)
            btn_load.set_rect(r_load)
            anchor_select.set_rect(r_anchor)
            sld_volume.set_rect(r_sld)

            slot_list.set_rect(canvas_rect)
            preset_list.set_rect(canvas_rect)

        if anchor_select.opened and event.type in (
            pygame.MOUSEBUTTONDOWN,
            pygame.MOUSEBUTTONUP,
            pygame.MOUSEMOTION,
            pygame.KEYDOWN,
        ):
            anchor_select.handle_event(event)
            continue

        if state["mode"] in ("samira", "penta"):
            slot_list.handle_event(event)
        elif state["mode"] == "preset":
            preset_list.handle_event(event)

        for e in ui:
            e.handle_event(event)

    if state["mode"] in ("samira", "penta"):
        slot_list.update(dt)
    elif state["mode"] == "preset":
        preset_list.update(dt)

    for e in ui:
        e.update(dt)

    # draw
    screen.fill(THEME.bg)

    if state["mode"] is None:
        draw_shadow_card(screen, canvas_rect, THEME.panel, radius=16, shadow_alpha=90)
        ttxt = FONT_16.render("CANVAS", True, THEME.text)
        screen.blit(ttxt, (canvas_rect.x + 16, canvas_rect.y + 16))
        guide = FONT_14.render("오른쪽에서 '사운드' 또는 '프리셋'을 선택하세요.", True, THEME.subtext)
        screen.blit(guide, (canvas_rect.x + 16, canvas_rect.y + 60))
    elif state["mode"] in ("samira", "penta"):
        slot_list.draw(screen)
    elif state["mode"] == "preset":
        preset_list.draw(screen)

    draw_shadow_card(screen, sidebar_rect, THEME.panel, radius=16, shadow_alpha=90)
    st = FONT_16.render("SOUNDS", True, THEME.text)
    screen.blit(st, (sidebar_rect.x + 16, sidebar_rect.y + 16))

    dbg_txt = "ON" if state["debug_window"] else "OFF"
    sam_txt = "Samira" if state["samira_active"] else "Not Samira"
    si = FONT_12.render(f"감지 창: {dbg_txt} / 감지 조건: {sam_txt}", True, THEME.subtext)
    screen.blit(si, (sidebar_rect.x + 16, sidebar_rect.y + 40))

    btn_open_samira.draw(screen)
    btn_open_penta.draw(screen)
    btn_debug.draw(screen)
    btn_open_presets.draw(screen)
    btn_save.draw(screen)
    btn_load.draw(screen)

    draw_shadow_card(screen, bottom_rect, THEME.panel, radius=16, shadow_alpha=90)
    anchor_select.draw(screen)
    sld_volume.draw(screen)

    if state["last_preset"]:
        info = FONT_12.render(f"Preset: {state['last_preset']}", True, THEME.subtext)
        screen.blit(info, (bottom_rect.x + 20, bottom_rect.y + 72))

    pygame.display.flip()

with det_ctl.lock:
    det_ctl.running = False

stop_music()
try:
    SFX_CHANNEL.stop()
except:
    pass

pygame.mixer.quit()
pygame.quit()
