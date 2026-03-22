import tkinter as tk
from tkinter import messagebox, ttk
from PIL import Image, ImageTk
import cv2
import os
import pandas as pd
import numpy as np
from datetime import datetime
import sys
import pickle
import threading
import hashlib
import hmac
import gspread
from google.oauth2.service_account import Credentials
from deepface import DeepFace


# -------- PATH SETUP --------
BASE_DIR   = os.path.dirname(sys.executable if getattr(sys, 'frozen', False) else __file__)
FACES_PATH = os.path.join(BASE_DIR, 'studentFaces')
PASS_FILE  = os.path.join(BASE_DIR, 'pass.txt')
EXCEL_FILE = os.path.join(BASE_DIR, 'Attendance.xlsx')
DB_FILE    = os.path.join(BASE_DIR, 'face_db.pkl')
CREDS_FILE = os.path.join(BASE_DIR, 'credentials.json')
SHEET_ID   = "1ti4Y48zS8cyZ5uJvsl5Uk_4E6qXZFlBCIlufJ1qxFJQ"

os.makedirs(FACES_PATH, exist_ok=True)


# -------- PASSWORD HASHING --------

def _hash_password(plain: str) -> str:
    """SHA-256 + random salt, stored as salt:hash (hex)."""
    salt = os.urandom(32).hex()
    h    = hashlib.sha256((salt + plain).encode()).hexdigest()
    return f"{salt}:{h}"

def _verify_password(plain: str, stored: str) -> bool:
    """Verify plain password against stored salt:hash string."""
    try:
        salt, h = stored.split(":", 1)
        expected = hashlib.sha256((salt + plain).encode()).hexdigest()
        return hmac.compare_digest(expected, h)
    except Exception:
        return False

def _migrate_password_file():
    """On first run, convert plain-text pass.txt to hashed format."""
    if not os.path.exists(PASS_FILE):
        with open(PASS_FILE, 'w') as f:
            f.write(_hash_password("admin123"))
        return
    with open(PASS_FILE, 'r') as f:
        content = f.read().strip()
    # If it doesn't look like a hashed value, migrate it
    if ':' not in content:
        with open(PASS_FILE, 'w') as f:
            f.write(_hash_password(content))

_migrate_password_file()

MODEL_NAME = "Facenet"
DETECTOR   = "opencv"
THRESHOLD  = 0.40


# -------- THEME --------
BG      = "#111318"   # main background
SURFACE = "#1c1f26"   # cards / panels
CARD    = "#23272f"   # inner card
BORDER  = "#2e333d"   # subtle borders
ACCENT  = "#4f8ef7"   # primary blue
ACCENT2 = "#3dd68c"   # green
TEXT    = "#f0f2f5"   # primary text
SUBTEXT = "#7a8394"   # secondary text
DANGER  = "#e5534b"   # red
WARNING = "#d4a72c"   # amber
SUCCESS = "#3dd68c"   # green
PURPLE  = "#8b5cf6"   # purple
MUTED   = "#2e333d"   # muted button bg

FONT_BODY  = ("Segoe UI", 11)
FONT_SMALL = ("Segoe UI", 9)
FONT_BTN   = ("Segoe UI Semibold", 11)
FONT_MONO  = ("Consolas", 11)
FONT_HEAD  = ("Segoe UI", 13, "bold")
FONT_TITLE = ("Segoe UI", 9)


def hex_to_rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def lerp_color(c1, c2, t):
    r1,g1,b1 = hex_to_rgb(c1)
    r2,g2,b2 = hex_to_rgb(c2)
    return "#{:02x}{:02x}{:02x}".format(
        int(r1+(r2-r1)*t), int(g1+(g2-g1)*t), int(b1+(b2-b1)*t))


# ══════════════════════════════════════════════
#  CUSTOM WIDGETS
# ══════════════════════════════════════════════

class FlatButton(tk.Frame):
    """Clean flat button — solid bg, 1px border, subtle hover."""
    def __init__(self, parent, text, command, color=ACCENT,
                 width=240, height=44, **kw):
        super().__init__(parent, bg=BG, **kw)
        self.command = command
        self.color   = color
        self.width   = width
        self.height  = height

        # Derive colors
        self._bg_idle   = lerp_color(color, "#000000", 0.72)
        self._bg_hover  = lerp_color(color, "#000000", 0.55)
        self._border    = lerp_color(color, "#000000", 0.40)

        self._inner = tk.Frame(self,
                               bg=self._bg_idle,
                               highlightbackground=self._border,
                               highlightthickness=1,
                               width=width, height=height)
        self._inner.pack_propagate(False)
        self._inner.pack()

        self._lbl = tk.Label(self._inner, text=text,
                             font=FONT_BTN,
                             fg="white", bg=self._bg_idle,
                             anchor="center")
        self._lbl.place(relx=0.5, rely=0.5, anchor="center")

        for widget in (self._inner, self._lbl):
            widget.bind("<Enter>",    lambda e: self._hover(True))
            widget.bind("<Leave>",    lambda e: self._hover(False))
            widget.bind("<Button-1>", lambda e: self._click())
            widget.config(cursor="hand2")

    def _hover(self, on):
        bg = self._bg_hover if on else self._bg_idle
        bd = self.color if on else self._border
        self._inner.config(bg=bg, highlightbackground=bd)
        self._lbl.config(bg=bg)

    def _click(self):
        if self.command: self.command()


# Keep alias so rest of code still works
GlowButton = FlatButton


class StatCard(tk.Frame):
    def __init__(self, parent, label, value, color=ACCENT, **kw):
        super().__init__(parent,
                         bg=SURFACE,
                         highlightbackground=BORDER,
                         highlightthickness=1,
                         **kw)
        # Left accent bar
        tk.Frame(self, bg=color, width=3).pack(side="left", fill="y")

        inner = tk.Frame(self, bg=SURFACE)
        inner.pack(side="left", padx=14, pady=12)

        tk.Label(inner, text=value,
                 font=("Segoe UI Semibold", 22),
                 fg=color, bg=SURFACE).pack(anchor="w")
        tk.Label(inner, text=label,
                 font=FONT_SMALL,
                 fg=SUBTEXT, bg=SURFACE).pack(anchor="w")


class SeparatorLine(tk.Frame):
    def __init__(self, parent, width=520, **kw):
        super().__init__(parent, bg=BORDER, height=1, width=width, **kw)
        self.pack_propagate(False)


class ModernDialog(tk.Toplevel):
    """Clean flat dark dialog."""
    def __init__(self, parent, title, prompt, secret=False, default=""):
        super().__init__(parent)
        self.result = None
        self.title(title)
        self.configure(bg=SURFACE)
        self.resizable(False, False)
        self.grab_set()
        pw = parent.winfo_rootx() + parent.winfo_width()//2
        ph = parent.winfo_rooty() + parent.winfo_height()//2
        self.geometry(f"380x180+{pw-190}+{ph-90}")

        # Top accent line
        tk.Frame(self, bg=ACCENT, height=2).pack(fill="x")

        tk.Label(self, text=prompt, font=FONT_BODY,
                 fg=TEXT, bg=SURFACE, anchor="w").pack(
                 fill="x", padx=24, pady=(18, 6))

        ef = tk.Frame(self, bg=BORDER, highlightthickness=0)
        ef.pack(fill="x", padx=24)
        self.var = tk.StringVar(value=default)
        e = tk.Entry(ef, textvariable=self.var, font=FONT_BODY,
                     bg=CARD, fg=TEXT, insertbackground=ACCENT,
                     relief="flat", bd=8,
                     show="●" if secret else "")
        e.pack(fill="x", ipady=5)
        e.focus_set()
        e.bind("<Return>", lambda _: self._ok())

        bf = tk.Frame(self, bg=SURFACE)
        bf.pack(pady=14, padx=24, fill="x")

        ok_btn = tk.Button(bf, text="Confirm", font=FONT_BTN,
                           bg=ACCENT, fg="white", relief="flat",
                           padx=20, pady=6, cursor="hand2",
                           activebackground=lerp_color(ACCENT,"#ffffff",0.15),
                           command=self._ok)
        ok_btn.pack(side="right", padx=(6,0))

        tk.Button(bf, text="Cancel", font=FONT_BTN,
                  bg=CARD, fg=SUBTEXT, relief="flat",
                  padx=20, pady=6, cursor="hand2",
                  activebackground=BORDER,
                  command=self.destroy).pack(side="right")

        self.wait_window()

    def _ok(self):
        self.result = self.var.get().strip()
        self.destroy()


def ask(parent, title, prompt, secret=False, default=""):
    return ModernDialog(parent, title, prompt, secret, default).result


# ══════════════════════════════════════════════
#  MINI BAR CHART (pure tkinter, no matplotlib)
# ══════════════════════════════════════════════

class BarChart(tk.Canvas):
    def __init__(self, parent, data, colors, width=700, height=200, **kw):
        super().__init__(parent, width=width, height=height,
                         bg=SURFACE, highlightthickness=0, **kw)
        self._draw(data, colors, width, height)

    def _draw(self, data, colors, W, H):
        if not data: return
        pad_l, pad_r, pad_t, pad_b = 50, 20, 20, 40
        n     = len(data)
        max_v = max(v for _,v in data) or 1
        bw    = (W - pad_l - pad_r) / n
        gap   = bw * 0.25

        # Grid lines
        for i in range(5):
            y = pad_t + (H-pad_t-pad_b) * i / 4
            self.create_line(pad_l, y, W-pad_r, y, fill=BORDER, dash=(4,4))
            val = int(max_v * (1 - i/4))
            self.create_text(pad_l-6, y, text=str(val),
                             anchor="e", font=FONT_SMALL, fill=SUBTEXT)

        for i, (label, val) in enumerate(data):
            x0 = pad_l + i*bw + gap/2
            x1 = x0 + bw - gap
            bar_h = (val / max_v) * (H - pad_t - pad_b)
            y0 = H - pad_b - bar_h
            y1 = H - pad_b
            c  = colors[i % len(colors)]
            # Bar
            self.create_rectangle(x0, y0, x1, y1, fill=c, outline="")
            # Value label on top
            self.create_text((x0+x1)/2, y0-6, text=str(val),
                             font=FONT_SMALL, fill=TEXT)
            # X label
            lbl = label[:8] if len(label)>8 else label
            self.create_text((x0+x1)/2, H-pad_b+14, text=lbl,
                             font=FONT_SMALL, fill=SUBTEXT, angle=0)


# ══════════════════════════════════════════════
#  MAIN APP
# ══════════════════════════════════════════════

class AttendanceApp:
    def __init__(self, window):
        self.window = window
        self.window.title("Attendance System")
        self.window.config(bg=BG)
        self.window.state("zoomed")       # fullscreen on Windows
        self.window.attributes("-fullscreen", False)
        self.window.iconbitmap(os.path.join(BASE_DIR, 'favicon.ico'))
        self.window.resizable(True, True)
        self.window.update_idletasks()

        self.known_embeddings = []
        self.known_names      = []
        self.totalS           = 0

        self._build_ui()
        self._load_db()

    # ──────────────────────────────────────────
    #  UI BUILD
    # ──────────────────────────────────────────

    def _build_ui(self):
        w = self.window
        w.configure(bg=BG)

        # ── Outer layout: sidebar + main content ──
        outer = tk.Frame(w, bg=BG)
        outer.place(relx=0, rely=0, relwidth=1, relheight=1)

        # Centered card panel
        panel = tk.Frame(outer, bg=BG)
        panel.place(relx=0.5, rely=0.5, anchor="center")

        # ── Top accent bar ──
        tk.Frame(panel, bg=ACCENT, height=3, width=560).pack(fill="x")

        # ── Header ──
        hdr = tk.Frame(panel, bg=SURFACE,
                       highlightbackground=BORDER, highlightthickness=1)
        hdr.pack(fill="x")

        hdr_inner = tk.Frame(hdr, bg=SURFACE)
        hdr_inner.pack(fill="x", padx=24, pady=16)

        # Title
        title_col = tk.Frame(hdr_inner, bg=SURFACE)
        title_col.pack(side="left")
        tk.Label(title_col, text="Attendance System",
                 font=("Segoe UI Semibold", 20),
                 fg=TEXT, bg=SURFACE).pack(anchor="w")
        tk.Label(title_col, text="DeepFace · Face Recognition",
                 font=FONT_SMALL, fg=SUBTEXT, bg=SURFACE).pack(anchor="w")

        # Clock pill
        clock_pill = tk.Frame(hdr_inner, bg=CARD,
                              highlightbackground=BORDER, highlightthickness=1)
        clock_pill.pack(side="right")
        self._clock_var = tk.StringVar()
        tk.Label(clock_pill, textvariable=self._clock_var,
                 font=FONT_MONO, fg=ACCENT2, bg=CARD,
                 padx=16, pady=8).pack()
        self._tick()

        # ── Stat cards row ──
        stats_row = tk.Frame(panel, bg=BG)
        stats_row.pack(fill="x", pady=(16, 0))

        students = len([f for f in os.listdir(FACES_PATH)
                        if f.lower().endswith(('.jpg', '.png', '.jpeg'))])
        records = 0
        if os.path.exists(EXCEL_FILE):
            try: records = len(pd.read_excel(EXCEL_FILE))
            except: pass

        for col, (val, lbl, clr) in enumerate([
            (str(students), "Registered Students", ACCENT),
            (str(records),  "Total Records",       ACCENT2),
            (datetime.now().strftime("%d %b %Y"), "Today", PURPLE),
        ]):
            card = StatCard(stats_row, lbl, val, clr)
            card.grid(row=0, column=col, padx=(0 if col==0 else 8, 0), sticky="nsew")
            stats_row.columnconfigure(col, weight=1)

        # ── Divider ──
        tk.Frame(panel, bg=BORDER, height=1).pack(fill="x", pady=16)

        # Section heading
        sh = tk.Frame(panel, bg=BG)
        sh.pack(fill="x", pady=(0, 8))
        tk.Label(sh, text="ACTIONS", font=("Segoe UI", 8, "bold"),
                 fg=SUBTEXT, bg=BG).pack(side="left")

        # ── Primary button — full width ──
        start_btn = tk.Button(panel,
                              text="▶   Start Attendance",
                              font=("Segoe UI Semibold", 13),
                              bg=SUCCESS,
                              fg="#0a1a10",
                              relief="flat",
                              pady=14,
                              cursor="hand2",
                              activebackground=lerp_color(SUCCESS,"#ffffff",0.15),
                              command=self.run_attendance)
        start_btn.pack(fill="x", pady=(0, 10))

        # ── 2-column button grid ──
        BW, BH = 268, 42
        for row_items in [
            [("＋  Add Student",    self.add_student,           ACCENT),
             ("🖼   Photo Gallery", self.show_gallery,          PURPLE)],
            [("◉  View Records",   self.show_attendance_table, PURPLE),
             ("📊  Analytics",      self.show_analytics,        ACCENT2)],
            [("📤  Google Sheet",   self.send_to_principal,     WARNING),
             ("🗑   Delete Today",  self.delete_today_data,     DANGER)],
            [("✕  Delete Student", self.delete_student,        MUTED),
             ("⚙  Password",       self.change_password,       MUTED)],
        ]:
            row_f = tk.Frame(panel, bg=BG)
            row_f.pack(fill="x", pady=4)
            for i, (label, cmd, color) in enumerate(row_items):
                bg_c  = color if color not in (MUTED,) else CARD
                fg_c  = "white"
                hover = lerp_color(bg_c, "#ffffff", 0.12)
                btn   = tk.Button(row_f, text=label,
                                  font=FONT_BTN,
                                  bg=bg_c, fg=fg_c,
                                  relief="flat", pady=10,
                                  cursor="hand2",
                                  activebackground=hover,
                                  activeforeground="white",
                                  command=cmd)
                btn.pack(side="left", fill="x", expand=True,
                         padx=(0 if i==0 else 8, 0))

        # ── Footer ──
        tk.Frame(panel, bg=BORDER, height=1).pack(fill="x", pady=(16,0))
        footer = tk.Frame(panel, bg=BG)
        footer.pack(fill="x", pady=8)
        tk.Label(footer, text="© 2026 CS Department Sri Chaitanya Techno School Kagadasapura 2 BLR",
                 font=("Segoe UI", 9), fg=SUBTEXT, bg=BG).pack(side="left")
        tk.Button(footer, text="Exit", font=FONT_SMALL,
                  bg=CARD, fg=SUBTEXT, relief="flat",
                  padx=16, pady=4, cursor="hand2",
                  activebackground=BORDER,
                  command=self.window.destroy).pack(side="right")

    def _on_resize(self, event=None):
        pass  # no background canvas needed in flat mode

    def _tick(self):
        self._clock_var.set(datetime.now().strftime("%H:%M:%S"))
        self.window.after(1000, self._tick)

    def _toast(self, msg, kind="info"):
        t = tk.Toplevel(self.window)
        t.overrideredirect(True); t.attributes("-topmost", True)
        c = {"info":ACCENT,"ok":SUCCESS,"warn":WARNING,"err":DANGER}.get(kind, ACCENT)
        t.configure(bg=SURFACE)
        pw = self.window.winfo_rootx() + self.window.winfo_width()//2
        ph = self.window.winfo_rooty() + self.window.winfo_height() - 80
        t.geometry(f"380x44+{pw-190}+{ph}")
        # Left color bar
        tk.Frame(t, bg=c, width=4).pack(side="left", fill="y")
        tk.Label(t, text=msg, font=FONT_BODY, bg=SURFACE,
                 fg=TEXT, pady=10, padx=14).pack(side="left", fill="both", expand=True)
        t.after(2800, t.destroy)

    # ──────────────────────────────────────────
    #  FACE DATABASE
    # ──────────────────────────────────────────

    def _load_db(self):
        self.known_embeddings.clear(); self.known_names.clear()
        if os.path.exists(DB_FILE):
            try:
                with open(DB_FILE,'rb') as f:
                    d = pickle.load(f)
                    self.known_embeddings = d.get('embeddings',[])
                    self.known_names      = d.get('names',[])
            except: pass
        self.totalS = len(self.known_names)

    def _save_db(self):
        with open(DB_FILE,'wb') as f:
            pickle.dump({'embeddings':self.known_embeddings,
                         'names':self.known_names}, f)

    def _get_embedding(self, img_bgr):
        try:
            rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
            r   = DeepFace.represent(img_path=rgb, model_name=MODEL_NAME,
                                     detector_backend=DETECTOR, enforce_detection=True)
            if r: return np.array(r[0]['embedding'])
        except: pass
        return None

    def _cosine(self, a, b):
        return np.dot(a,b)/(np.linalg.norm(a)*np.linalg.norm(b)+1e-10)

    def _recognize(self, emb):
        if not self.known_embeddings: return "Unknown"
        sims = [self._cosine(emb,e) for e in self.known_embeddings]
        best = int(np.argmax(sims))
        return self.known_names[best] if sims[best]>=THRESHOLD else "Unknown"

    def load_database(self):
        self._load_db(); return self.totalS > 0

    # ──────────────────────────────────────────
    #  PASSWORD
    # ──────────────────────────────────────────

    def _verify_admin(self, prompt="Admin Password:") -> bool:
        """Show password dialog and verify against stored hash. Returns True if correct."""
        entered = ask(self.window, "Security", prompt, secret=True)
        if not entered:
            return False
        with open(PASS_FILE, 'r') as f:
            stored = f.read().strip()
        if _verify_password(entered, stored):
            return True
        self._toast("Access denied — incorrect password", "err")
        return False

    def change_password(self):
        if not self._verify_admin("Enter Current Password:"):
            return
        new = ask(self.window, "Security", "Enter New Password:", secret=True)
        if not new:
            return
        confirm = ask(self.window, "Security", "Confirm New Password:", secret=True)
        if new != confirm:
            self._toast("Passwords do not match!", "err")
            return
        if len(new) < 6:
            self._toast("Password must be at least 6 characters!", "warn")
            return
        with open(PASS_FILE, 'w') as f:
            f.write(_hash_password(new))
        self._toast("✓  Password updated securely!", "ok")

    # ──────────────────────────────────────────
    #  ADD STUDENT
    # ──────────────────────────────────────────

    def add_student(self):
        if not self._verify_admin(): return
        name = ask(self.window,"Add Student","Student Name:")
        scs  = ask(self.window,"Add Student","SCS / Roll Number:")
        cls  = ask(self.window,"Add Student","Class / Section:")
        if not (name and scs and cls): return

        sid  = f"{name}_{scs}_{cls}"
        cap  = cv2.VideoCapture(0)
        fc   = cv2.CascadeClassifier(cv2.data.haarcascades+'haarcascade_frontalface_default.xml')
        self._toast("Press  S  to capture face","info")

        while True:
            ret, frame = cap.read()
            if not ret: break
            ov = frame.copy()
            cv2.rectangle(ov,(0,0),(frame.shape[1],50),(0,0,0),-1)
            cv2.addWeighted(ov,0.6,frame,0.4,0,frame)
            cv2.putText(frame,f"Adding: {name}  |  [S] capture",
                        (10,32),cv2.FONT_HERSHEY_SIMPLEX,0.65,(150,230,255),2)
            gray  = cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
            faces = fc.detectMultiScale(gray,1.1,5,minSize=(80,80))
            for (x,y,fw,fh) in faces:
                cv2.rectangle(frame,(x,y),(x+fw,y+fh),(80,220,140),2)
            cv2.imshow("Add Student",frame)

            if cv2.waitKey(1)&0xFF == ord('s'):
                emb = self._get_embedding(frame)
                if emb is not None:
                    if len(faces)>0:
                        x,y,fw,fh=faces[0]
                        cv2.imwrite(os.path.join(FACES_PATH,f"{sid}.jpg"),
                                    frame[y:y+fh,x:x+fw])
                    if sid in self.known_names:
                        i=self.known_names.index(sid)
                        self.known_names.pop(i); self.known_embeddings.pop(i)
                    self.known_names.append(sid)
                    self.known_embeddings.append(emb)
                    self.totalS = len(self.known_names)
                    self._save_db()
                    cap.release(); cv2.destroyAllWindows()
                    self._toast(f"✓  {name} added!","ok"); return
                else:
                    self._toast("No face detected – try again","warn")

        cap.release(); cv2.destroyAllWindows()

    # ──────────────────────────────────────────
    #  DELETE STUDENT
    # ──────────────────────────────────────────

    def delete_student(self):
        if not self._verify_admin(): return

        win = tk.Toplevel(self.window)
        win.title("Delete Student"); win.geometry("380x440")
        win.configure(bg=SURFACE); win.grab_set()
        tk.Label(win,text="SELECT STUDENT TO REMOVE",
                 font=("Courier New",9,"bold"),fg=SUBTEXT,bg=SURFACE).pack(pady=(16,4))

        lbf = tk.Frame(win,bg=BORDER); lbf.pack(fill="both",expand=True,padx=20,pady=6)
        sb  = tk.Scrollbar(lbf); sb.pack(side="right",fill="y")
        lb  = tk.Listbox(lbf,yscrollcommand=sb.set,font=FONT_BODY,bg=CARD,fg=TEXT,
                         selectbackground=DANGER,selectforeground="white",
                         relief="flat",bd=8,activestyle="none",highlightthickness=0)
        lb.pack(fill="both",expand=True); sb.config(command=lb.yview)
        for n in self.known_names: lb.insert(tk.END,"  "+n)

        def delete():
            sel=lb.curselection()
            if not sel: return
            sid=lb.get(sel).strip()
            if messagebox.askyesno("Confirm",f"Delete {sid}?",parent=win):
                idx=self.known_names.index(sid)
                self.known_names.pop(idx); self.known_embeddings.pop(idx)
                self.totalS=len(self.known_names); self._save_db()
                p=os.path.join(FACES_PATH,f"{sid}.jpg")
                if os.path.exists(p): os.remove(p)
                lb.delete(sel); self._toast(f"Removed: {sid}","warn")

        GlowButton(win,"✕  DELETE SELECTED",delete,
                   color=DANGER,width=300,height=42).pack(pady=12)

    # ──────────────────────────────────────────
    #  ATTENDANCE
    # ──────────────────────────────────────────

    def run_attendance(self):
        if not self.load_database():
            self._toast("No students registered yet!","warn"); return

        log     = {}
        cap     = cv2.VideoCapture(0)
        frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        stopped = [False]; saved=[False]
        fc      = cv2.CascadeClassifier(cv2.data.haarcascades+'haarcascade_frontalface_default.xml')
        fc_n    = 0; last=[]

        def click(event,x,y,flags,param):
            bw,bh,by=130,46,frame_h-60
            if 20<=x<=20+bw and by<=y<=by+bh: stopped[0]=True
            if frame_w-20-bw<=x<=frame_w-20 and by<=y<=by+bh: saved[0]=True

        cv2.namedWindow("Attendance")
        cv2.setMouseCallback("Attendance",click)

        while True:
            ret,frame=cap.read()
            if not ret: break
            fc_n+=1
            if fc_n%5==0:
                gray  = cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
                faces = fc.detectMultiScale(gray,1.1,5,minSize=(80,80))
                last  = []
                for (x,y,fw,fh) in faces:
                    try:
                        r    = DeepFace.represent(
                            img_path=cv2.cvtColor(frame[y:y+fh,x:x+fw],cv2.COLOR_BGR2RGB),
                            model_name=MODEL_NAME,detector_backend="skip",enforce_detection=False)
                        name = self._recognize(np.array(r[0]['embedding']))
                    except: name="Unknown"
                    if name!="Unknown" and name not in log:
                        log[name]=datetime.now().strftime("%H:%M:%S")
                    last.append((name,x,y,fw,fh))

            for (name,x,y,fw,fh) in last:
                col   = (80,220,140) if name!="Unknown" else (80,80,255)
                label = name.split('_')[0] if name!="Unknown" else "Unknown"
                cv2.rectangle(frame,(x,y),(x+fw,y+fh),col,2)
                cv2.rectangle(frame,(x,y-28),(x+fw,y),col,-1)
                cv2.putText(frame,label,(x+4,y-8),cv2.FONT_HERSHEY_SIMPLEX,0.65,(0,0,0),2)

            cv2.rectangle(frame,(0,0),(frame_w,52),(0,0,0),-1)
            cv2.putText(frame,"ATTENDANCE SYSTEM",(12,18),cv2.FONT_HERSHEY_SIMPLEX,0.6,(90,200,255),2)
            cv2.putText(frame,
                f"Present: {len(log)}  |  Total: {self.totalS}  |  {datetime.now().strftime('%H:%M:%S')}",
                (12,42),cv2.FONT_HERSHEY_SIMPLEX,0.55,(200,230,200),1)

            bw,bh,by=130,46,frame_h-60
            cv2.rectangle(frame,(20,by),(20+bw,by+bh),(60,60,200),-1)
            cv2.putText(frame,"STOP",(40,by+30),cv2.FONT_HERSHEY_SIMPLEX,0.7,(255,255,255),2)
            cv2.rectangle(frame,(frame_w-20-bw,by),(frame_w-20,by+bh),(40,160,90),-1)
            cv2.putText(frame,"SAVE",(frame_w-20-bw+16,by+30),cv2.FONT_HERSHEY_SIMPLEX,0.7,(255,255,255),2)

            cv2.imshow("Attendance",frame); cv2.waitKey(1)
            if stopped[0]: break
            if saved[0]:
                cap.release(); cv2.destroyAllWindows()
                self.save_attendance(log); return

        cap.release(); cv2.destroyAllWindows()

    # ──────────────────────────────────────────
    #  SAVE
    # ──────────────────────────────────────────

    def save_attendance(self, log):
        data=[]
        for sid in self.known_names:
            p=sid.split('_')
            data.append({"Name":p[0],"SCS":p[1] if len(p)>1 else "",
                          "Class":p[2] if len(p)>2 else "",
                          "Status":"Present" if sid in log else "Absent",
                          "Time":log.get(sid,"--"),
                          "Date":datetime.now().strftime("%Y-%m-%d")})
        pd.DataFrame(data).to_excel(EXCEL_FILE,index=False)
        self._toast("✓  Attendance saved to Excel!","ok")

    # ──────────────────────────────────────────
    #  VIEW RECORDS  (with search + filter)
    # ──────────────────────────────────────────

    def show_attendance_table(self):
        if not self._verify_admin(): return
        if not os.path.exists(EXCEL_FILE):
            self._toast("No records found yet!","warn"); return

        df  = pd.read_excel(EXCEL_FILE)
        win = tk.Toplevel(self.window)
        win.title("Attendance Records"); win.geometry("860x580")
        win.configure(bg=SURFACE)

        # ── Title ──
        tk.Label(win,text="ATTENDANCE RECORDS",font=FONT_HEAD,
                 fg=TEXT,bg=SURFACE).pack(pady=(14,2))

        # ── Search + Filter bar ──
        bar = tk.Frame(win,bg=SURFACE); bar.pack(fill="x",padx=16,pady=6)

        tk.Label(bar,text="Search:",font=FONT_SMALL,fg=SUBTEXT,bg=SURFACE).pack(side="left")
        search_var = tk.StringVar()
        se = tk.Entry(bar,textvariable=search_var,font=FONT_BODY,
                      bg=CARD,fg=TEXT,insertbackground=ACCENT,
                      relief="flat",bd=6,width=20)
        se.pack(side="left",padx=(4,16),ipady=3)

        tk.Label(bar,text="Status:",font=FONT_SMALL,fg=SUBTEXT,bg=SURFACE).pack(side="left")
        filter_var = tk.StringVar(value="All")
        cb = ttk.Combobox(bar,textvariable=filter_var,
                          values=["All","Present","Absent"],
                          state="readonly",width=10,font=FONT_BODY)
        cb.pack(side="left",padx=(4,16))

        tk.Label(bar,text="Date:",font=FONT_SMALL,fg=SUBTEXT,bg=SURFACE).pack(side="left")
        dates = ["All"] + sorted(df["Date"].astype(str).unique().tolist(), reverse=True)
        date_var = tk.StringVar(value="All")
        dcb = ttk.Combobox(bar,textvariable=date_var,values=dates,
                           state="readonly",width=12,font=FONT_BODY)
        dcb.pack(side="left",padx=(4,16))

        count_var = tk.StringVar()
        tk.Label(bar,textvariable=count_var,font=FONT_SMALL,
                 fg=ACCENT2,bg=SURFACE).pack(side="right",padx=8)

        # ── Treeview ──
        style = ttk.Style(); style.theme_use("clam")
        style.configure("Dark.Treeview",background=CARD,foreground=TEXT,
                        rowheight=28,fieldbackground=CARD,font=FONT_BODY)
        style.configure("Dark.Treeview.Heading",background=SURFACE,
                        foreground=ACCENT,font=("Georgia",10,"bold"))
        style.map("Dark.Treeview",
                  background=[("selected",ACCENT)],
                  foreground=[("selected","white")])

        frm=tk.Frame(win,bg=SURFACE); frm.pack(fill="both",expand=True,padx=16,pady=4)
        sb=ttk.Scrollbar(frm); sb.pack(side="right",fill="y")
        cols=list(df.columns)
        tree=ttk.Treeview(frm,columns=cols,show="headings",
                          style="Dark.Treeview",yscrollcommand=sb.set)
        sb.config(command=tree.yview)

        cw={"Name":140,"SCS":100,"Class":100,"Status":90,"Time":90,"Date":110}
        for col in cols:
            tree.heading(col,text=col,
                command=lambda c=col: _sort(c))
            tree.column(col,width=cw.get(col,120),anchor="center")

        tree.tag_configure("present",foreground=SUCCESS)
        tree.tag_configure("absent", foreground=DANGER)
        tree.tag_configure("even",   background=CARD)
        tree.tag_configure("odd",    background=lerp_color(CARD,SURFACE,0.5))
        tree.pack(fill="both",expand=True)

        sort_state = {}

        def _sort(col):
            rev = sort_state.get(col, False)
            sorted_df = _filtered().sort_values(col, ascending=not rev,
                                                key=lambda x: x.astype(str))
            sort_state[col] = not rev
            _populate(sorted_df)

        def _filtered():
            q   = search_var.get().strip().lower()
            st  = filter_var.get()
            dt  = date_var.get()
            out = df.copy()
            if q:
                out = out[out.apply(
                    lambda r: q in str(r.get("Name","")).lower() or
                              q in str(r.get("SCS","")).lower(), axis=1)]
            if st != "All":
                out = out[out["Status"].astype(str).str.lower() == st.lower()]
            if dt != "All":
                out = out[out["Date"].astype(str) == dt]
            return out

        def _populate(data=None):
            tree.delete(*tree.get_children())
            rows = _filtered() if data is None else data
            count_var.set(f"{len(rows)} records")
            for i,(_, row) in enumerate(rows.iterrows()):
                tags=("even" if i%2==0 else "odd",)
                s=str(row.get("Status","")).lower()
                if s=="present": tags+=("present",)
                elif s=="absent": tags+=("absent",)
                tree.insert("","end",values=list(row),tags=tags)

        _populate()
        search_var.trace_add("write", lambda *_: _populate())
        filter_var.trace_add("write", lambda *_: _populate())
        date_var.trace_add("write",   lambda *_: _populate())

        # ── Bottom buttons ──
        bot=tk.Frame(win,bg=SURFACE); bot.pack(pady=8)
        GlowButton(bot,"⬇  Export Filtered to Excel",
                   lambda: self._export_filtered(_filtered()),
                   color=ACCENT,width=240,height=36).pack(side="left",padx=6)
        GlowButton(bot,"📤  Upload to Sheet",
                   lambda: self._email_dataframe(_filtered(),"Filtered Attendance Report"),
                   color=WARNING,width=220,height=36).pack(side="left",padx=6)

    def _export_filtered(self, df):
        out = os.path.join(BASE_DIR, f"Attendance_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")
        df.to_excel(out, index=False)
        self._toast(f"Exported: {os.path.basename(out)}", "ok")

    # ──────────────────────────────────────────
    #  DELETE TODAY'S DATA
    # ──────────────────────────────────────────

    def delete_today_data(self):
        today = datetime.now().strftime("%Y-%m-%d")

        # Confirm
        if not messagebox.askyesno(
            "Delete Today's Data",
            f"This will permanently delete all attendance records\n"
            f"for {today} from Excel AND Google Sheets.\n\n"
            f"Are you sure?",
            icon="warning"
        ):
            return

        # Password check
        if not self._verify_admin(): return

        deleted_excel  = False
        deleted_sheets = False

        # ── Delete from Excel ──
        if os.path.exists(EXCEL_FILE):
            try:
                df = pd.read_excel(EXCEL_FILE)
                before = len(df)
                df = df[df["Date"].astype(str) != today]
                after  = len(df)
                df.to_excel(EXCEL_FILE, index=False)
                deleted_excel = True
                removed = before - after
            except Exception as ex:
                self._toast(f"Excel error: {ex}","err"); return
        else:
            removed = 0
            deleted_excel = True

        # ── Delete from Google Sheets ──
        def _delete_sheet():
            nonlocal deleted_sheets
            if not os.path.exists(CREDS_FILE):
                self.window.after(0, lambda: self._toast(
                    f"✓  Deleted {removed} rows from Excel (no credentials.json for Sheets)","ok"))
                return
            try:
                scopes = [
                    "https://www.googleapis.com/auth/spreadsheets",
                    "https://www.googleapis.com/auth/drive",
                ]
                creds  = Credentials.from_service_account_file(CREDS_FILE, scopes=scopes)
                client = gspread.authorize(creds)
                sh     = client.open_by_key(SHEET_ID)

                # Delete the tab named after today
                try:
                    ws = sh.worksheet(today)
                    sh.del_worksheet(ws)
                    deleted_sheets = True
                except gspread.WorksheetNotFound:
                    pass  # Tab didn't exist, that's fine

                # Also remove today's row from Summary tab
                try:
                    summary = sh.worksheet("Summary")
                    rows    = summary.get_all_values()
                    # Find and delete rows where first column matches today
                    for i, row in enumerate(rows[1:], start=2):  # skip header
                        if row and row[0] == today:
                            summary.delete_rows(i)
                            break
                except gspread.WorksheetNotFound:
                    pass

                self.window.after(0, lambda: self._toast(
                    f"✓  Deleted {removed} Excel rows + Sheet tab '{today}'","ok"))

            except Exception as ex:
                self.window.after(0, lambda: self._toast(
                    f"Excel done but Sheets error: {ex}","warn"))

        import threading
        threading.Thread(target=_delete_sheet, daemon=True).start()

    # ──────────────────────────────────────────
    #  ANALYTICS DASHBOARD
    # ──────────────────────────────────────────

    def show_analytics(self):
        if not self._verify_admin(): return
        if not os.path.exists(EXCEL_FILE):
            self._toast("No records found yet!","warn"); return

        df  = pd.read_excel(EXCEL_FILE)
        win = tk.Toplevel(self.window)
        win.title("Analytics Dashboard"); win.geometry("780x620")
        win.configure(bg=BG)

        tk.Label(win,text="ANALYTICS DASHBOARD",font=("Georgia",15,"bold"),
                 fg=TEXT,bg=BG).pack(pady=(18,2))
        tk.Label(win,text=f"Based on {len(df)} records",
                 font=FONT_SMALL,fg=SUBTEXT,bg=BG).pack()

        # ── KPI cards ──
        kpi_frame = tk.Frame(win,bg=BG); kpi_frame.pack(fill="x",padx=20,pady=12)
        total     = len(df)
        present   = len(df[df["Status"].astype(str).str.lower()=="present"])
        absent    = total - present
        pct       = f"{present/total*100:.1f}%" if total else "0%"
        students  = df["Name"].nunique() if "Name" in df.columns else 0

        for col,(val,lbl,clr) in enumerate([
            (str(present), "Present",    SUCCESS),
            (str(absent),  "Absent",     DANGER),
            (pct,          "Rate",       ACCENT),
            (str(students),"Students",   PURPLE),
        ]):
            card=StatCard(kpi_frame,lbl,val,clr)
            card.grid(row=0,column=col,padx=6,sticky="nsew")
            kpi_frame.columnconfigure(col,weight=1)

        # ── Tab selector ──
        tab_var = tk.StringVar(value="by_date")
        tab_row = tk.Frame(win,bg=BG); tab_row.pack(pady=(8,0))

        def make_tab(label, value):
            def cmd():
                tab_var.set(value); refresh()
            b=tk.Button(tab_row,text=label,font=FONT_SMALL,
                        relief="flat",padx=12,pady=4,cursor="hand2",
                        command=cmd)
            b.pack(side="left",padx=3)
            return b

        t1=make_tab("By Date",    "by_date")
        t2=make_tab("By Student", "by_student")
        t3=make_tab("By Class",   "by_class")

        chart_frame=tk.Frame(win,bg=BG); chart_frame.pack(fill="both",expand=True,padx=20,pady=10)

        CHART_COLORS = [ACCENT,SUCCESS,PURPLE,ACCENT2,WARNING,DANGER,"#e879f9","#38bdf8"]

        def refresh():
            for w in chart_frame.winfo_children(): w.destroy()
            mode = tab_var.get()

            if mode=="by_date":
                grp = df[df["Status"].astype(str).str.lower()=="present"]\
                        .groupby("Date").size().reset_index(name="count")
                grp["Date"]=grp["Date"].astype(str)
                data=list(zip(grp["Date"],grp["count"]))
                title="Present count per date"

            elif mode=="by_student":
                if "Name" not in df.columns:
                    tk.Label(chart_frame,text="No Name column",fg=TEXT,bg=BG).pack(); return
                grp  = df[df["Status"].astype(str).str.lower()=="present"]\
                         .groupby("Name").size().reset_index(name="count")\
                         .sort_values("count",ascending=False).head(15)
                data = list(zip(grp["Name"],grp["count"]))
                title= "Top students by days present"

            else:  # by_class
                if "Class" not in df.columns:
                    tk.Label(chart_frame,text="No Class column",fg=TEXT,bg=BG).pack(); return
                grp  = df[df["Status"].astype(str).str.lower()=="present"]\
                         .groupby("Class").size().reset_index(name="count")
                data = list(zip(grp["Class"].astype(str),grp["count"]))
                title= "Present count by class"

            tk.Label(chart_frame,text=title,font=FONT_SMALL,
                     fg=SUBTEXT,bg=BG).pack(anchor="w")

            if not data:
                tk.Label(chart_frame,text="No data to display",
                         font=FONT_BODY,fg=SUBTEXT,bg=BG).pack(pady=40); return

            BarChart(chart_frame,data,CHART_COLORS,width=720,height=240).pack(pady=4)

            # Absent list
            absent_df = df[df["Status"].astype(str).str.lower()=="absent"]
            if len(absent_df):
                tk.Label(chart_frame,
                         text=f"⚠  {len(absent_df)} absent entries today",
                         font=FONT_SMALL,fg=DANGER,bg=BG).pack(anchor="w",pady=(6,0))

        refresh()

    # ──────────────────────────────────────────
    #  PHOTO GALLERY
    # ──────────────────────────────────────────

    def show_gallery(self):
        if not self._verify_admin(): return

        files = [f for f in os.listdir(FACES_PATH)
                 if f.lower().endswith(('.jpg','.png','.jpeg'))]

        win = tk.Toplevel(self.window)
        win.title("Student Photo Gallery")
        win.geometry("860x600")
        win.configure(bg=BG)

        # Header
        hdr = tk.Frame(win, bg=BG); hdr.pack(fill="x", padx=20, pady=(16,8))
        tk.Label(hdr, text="STUDENT GALLERY", font=("Segoe UI Light", 18),
                 fg=TEXT, bg=BG).pack(side="left")
        tk.Label(hdr, text=f"{len(files)} registered",
                 font=FONT_SMALL, fg=SUBTEXT, bg=BG).pack(side="left", padx=12)

        # Search
        sv = tk.StringVar()
        se = tk.Entry(hdr, textvariable=sv, font=FONT_BODY,
                      bg=CARD, fg=TEXT, insertbackground=ACCENT,
                      relief="flat", bd=6, width=20)
        se.pack(side="right", ipady=4)
        tk.Label(hdr, text="Search:", font=FONT_SMALL,
                 fg=SUBTEXT, bg=BG).pack(side="right", padx=4)

        # Scrollable canvas grid
        outer = tk.Frame(win, bg=BG); outer.pack(fill="both", expand=True, padx=10, pady=4)
        vsb   = tk.Scrollbar(outer, orient="vertical", bg=BG)
        vsb.pack(side="right", fill="y")
        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0,
                           yscrollcommand=vsb.set)
        canvas.pack(fill="both", expand=True)
        vsb.config(command=canvas.yview)

        grid_frame = tk.Frame(canvas, bg=BG)
        canvas_win = canvas.create_window((0,0), window=grid_frame, anchor="nw")

        def on_frame_configure(e):
            canvas.configure(scrollregion=canvas.bbox("all"))
        grid_frame.bind("<Configure>", on_frame_configure)

        def on_canvas_configure(e):
            canvas.itemconfig(canvas_win, width=e.width)
        canvas.bind("<Configure>", on_canvas_configure)

        canvas.bind_all("<MouseWheel>",
            lambda e: canvas.yview_scroll(-1*(e.delta//120), "units"))

        self._gallery_images = []  # keep references

        THUMB = 120
        COLS  = 6

        def populate(query=""):
            for w in grid_frame.winfo_children(): w.destroy()
            self._gallery_images.clear()

            shown = [f for f in files
                     if query.lower() in f.lower()] if query else files

            if not shown:
                tk.Label(grid_frame, text="No students found",
                         font=FONT_BODY, fg=SUBTEXT, bg=BG).pack(pady=40)
                return

            for idx, fname in enumerate(shown):
                row = idx // COLS
                col = idx % COLS

                # Card frame
                card = tk.Frame(grid_frame, bg=CARD,
                                highlightbackground=BORDER, highlightthickness=1)
                card.grid(row=row, column=col, padx=6, pady=6)

                # Photo
                path = os.path.join(FACES_PATH, fname)
                try:
                    img  = Image.open(path).resize((THUMB, THUMB), Image.LANCZOS)
                    # Circular crop
                    mask = Image.new("L", (THUMB, THUMB), 0)
                    from PIL import ImageDraw as ID
                    ID.Draw(mask).ellipse((0,0,THUMB,THUMB), fill=255)
                    img.putalpha(mask)
                    photo = ImageTk.PhotoImage(img)
                except Exception:
                    photo = None

                if photo:
                    self._gallery_images.append(photo)
                    lbl = tk.Label(card, image=photo, bg=CARD)
                    lbl.pack(padx=8, pady=(8,4))
                else:
                    tk.Label(card, text="?", font=("Segoe UI",28),
                             fg=SUBTEXT, bg=CARD,
                             width=5, height=3).pack(padx=8, pady=(8,4))

                # Name
                parts = fname.replace(".jpg","").replace(".png","").split("_")
                name  = parts[0] if parts else fname
                scs   = parts[1] if len(parts)>1 else ""
                cls   = parts[2] if len(parts)>2 else ""

                tk.Label(card, text=name, font=("Segoe UI Semibold", 9),
                         fg=TEXT, bg=CARD).pack()
                tk.Label(card, text=f"{scs} · {cls}" if scs else "",
                         font=("Segoe UI", 8), fg=SUBTEXT, bg=CARD).pack(pady=(0,6))

        populate()
        sv.trace_add("write", lambda *_: populate(sv.get()))

    # ──────────────────────────────────────────
    #  GOOGLE SHEETS UPLOAD
    # ──────────────────────────────────────────

    def send_to_principal(self):
        if not self._verify_admin(): return
        if not os.path.exists(EXCEL_FILE):
            self._toast("No attendance data to upload!","warn"); return
        if not os.path.exists(CREDS_FILE):
            self._toast("credentials.json not found in project folder!","err"); return

        self._toast("Uploading to Google Sheets…","info")

        def _upload():
            try:
                df = pd.read_excel(EXCEL_FILE)

                scopes = [
                    "https://www.googleapis.com/auth/spreadsheets",
                    "https://www.googleapis.com/auth/drive",
                ]
                creds  = Credentials.from_service_account_file(CREDS_FILE, scopes=scopes)
                client = gspread.authorize(creds)
                sh     = client.open_by_key(SHEET_ID)

                # Use a sheet named after today's date, create if missing
                tab_name = datetime.now().strftime("%Y-%m-%d")
                try:
                    ws = sh.worksheet(tab_name)
                    ws.clear()
                except gspread.WorksheetNotFound:
                    ws = sh.add_worksheet(title=tab_name, rows=500, cols=10)

                # Write header + data
                header = list(df.columns)
                rows   = df.astype(str).values.tolist()
                ws.update([header] + rows)

                # Bold the header row
                ws.format("A1:Z1", {"textFormat": {"bold": True}})

                # Color present/absent rows
                present_color = {"red":0.18,"green":0.55,"blue":0.34}
                absent_color  = {"red":0.6, "green":0.18,"blue":0.18}
                for i, row in enumerate(rows, start=2):
                    status = row[header.index("Status")].lower() if "Status" in header else ""
                    color  = present_color if status=="present" else absent_color
                    ws.format(f"A{i}:Z{i}", {
                        "backgroundColor": color,
                        "textFormat": {"foregroundColor":{"red":1,"green":1,"blue":1}}
                    })

                # Also update a "Summary" sheet
                try:
                    summary = sh.worksheet("Summary")
                except gspread.WorksheetNotFound:
                    summary = sh.add_worksheet(title="Summary", rows=100, cols=5)

                total   = len(df)
                present = len(df[df["Status"].astype(str).str.lower()=="present"])
                absent  = total - present
                rate    = f"{present/total*100:.1f}%" if total else "0%"

                # Append a summary row
                existing = summary.get_all_values()
                if not existing:
                    summary.update([["Date","Total","Present","Absent","Rate"]])
                summary.append_row([tab_name, total, present, absent, rate])

                self.window.after(0, lambda: self._toast(
                    f"✓  Uploaded to Sheet: {tab_name}","ok"))

            except Exception as ex:
                self.window.after(0, lambda: self._toast(
                    f"Upload failed: {ex}","err"))

        threading.Thread(target=_upload, daemon=True).start()

    def _email_dataframe(self, df, subject):
        """Upload filtered data to a temporary sheet tab."""
        self._toast("Uploading filtered data…","info")
        def _upload():
            try:
                scopes = [
                    "https://www.googleapis.com/auth/spreadsheets",
                    "https://www.googleapis.com/auth/drive",
                ]
                creds  = Credentials.from_service_account_file(CREDS_FILE, scopes=scopes)
                client = gspread.authorize(creds)
                sh     = client.open_by_key(SHEET_ID)
                tab    = f"Export_{datetime.now().strftime('%H%M%S')}"
                ws     = sh.add_worksheet(title=tab, rows=500, cols=10)
                ws.update([list(df.columns)] + df.astype(str).values.tolist())
                self.window.after(0, lambda: self._toast(f"✓  Exported to tab: {tab}","ok"))
            except Exception as ex:
                self.window.after(0, lambda: self._toast(f"Export failed: {ex}","err"))
        threading.Thread(target=_upload, daemon=True).start()


# ══════════════════════════════════════════════
#  ENTRY
# ══════════════════════════════════════════════
if __name__ == "__main__":
    root = tk.Tk()
    app  = AttendanceApp(root)
    root.mainloop()