import cv2
import numpy as np
import requests
import json
import time
import threading
from PIL import ImageGrab
from collections import Counter
import tkinter as tk
from tkinter import ttk, colorchooser, messagebox
import win32evtlog

# --- Настройки ---
ESP_IP = "192.168.0.132"  # IP устройства
PORT = 80
url = f"http://{ESP_IP}:{PORT}"
headers = {'Content-Type': 'application/json'}
SCREENSHOT_INTERVAL = 0.1
TRANSITION_TIME = 1.0

# --- Глобальные переменные ---
screen_mode_active = False
screen_mode_thread = None
screen_mode_stop_event = threading.Event()

running = True
is_esp_connected = False
screen_color_mode = False
current_color = [0, 0, 0]
target_color = [0, 0, 0]
current_brightness = 100
target_brightness = 100
notifications_enabled = True
face_detected = False

# --- Загрузка модели для распознавания лиц ---
faceProto = "opencv_face_detector.pbtxt"
faceModel = "opencv_face_detector_uint8.pb"
faceNet = cv2.dnn.readNet(faceModel, faceProto)

# --- Функция для определения лиц ---
def highlightFace(net, frame, conf_threshold=0.7):
    frameOpencvDnn = frame.copy()
    frameHeight = frameOpencvDnn.shape[0]
    frameWidth = frameOpencvDnn.shape[1]
    blob = cv2.dnn.blobFromImage(frameOpencvDnn, 1.0, (300, 300), [104, 117, 123], True, False)
    net.setInput(blob)
    detections = net.forward()
    faceBoxes = []
    for i in range(detections.shape[2]):
        confidence = detections[0, 0, i, 2]
        if confidence > conf_threshold:
            x1 = int(detections[0, 0, i, 3] * frameWidth)
            y1 = int(detections[0, 0, i, 4] * frameHeight)
            x2 = int(detections[0, 0, i, 5] * frameWidth)
            y2 = int(detections[0, 0, i, 6] * frameHeight)
            faceBoxes.append([x1, y1, x2, y2])
            cv2.rectangle(frameOpencvDnn, (x1, y1), (x2, y2), (0, 255, 0), max(1, int(round(frameHeight/150))))
    return frameOpencvDnn, faceBoxes

# --- Подключение к ESP ---
def connect_to_esp():
    global is_esp_connected
    try:
        response = requests.get(url + "/status", timeout=5)
        if response.status_code == 200:
            print("Успешно подключено к ESP8266")
            send_command({"isPCConnected": True})
            is_esp_connected = True
            return True
        else:
            print(f"Ошибка подключения: {response.status_code}")
            is_esp_connected = False
            return False
    except Exception as e:
        print(f"Ошибка: {e}")
        is_esp_connected = False
        return False

def disconnect_from_esp():
    global running, is_esp_connected
    send_command({"isPCConnected": False})
    running = False
    is_esp_connected = False
    print("Отключено от ESP8266")
    

def send_command(data):
    try:
        requests.post(url + "/command", headers=headers, data=json.dumps(data), timeout=1)
    except:
        pass

# --- Анализ цвета ---
def get_dominant_color(image, resize_factor=0.2):
    try:
        width, height = image.size
        new_width = max(1, int(width * resize_factor))
        new_height = max(1, int(height * resize_factor))
        resized_image = image.resize((new_width, new_height))
        pixels = list(resized_image.getdata())
        color_counts = Counter(pixels)
        dominant_color = color_counts.most_common(1)[0][0]
        return list(dominant_color)
    except:
        return [0, 0, 0]

# --- Поток анализа цвета с экрана ---
def screen_color_analyzer(stop_event):
    global current_color
    while not stop_event.is_set():
        try:
            screenshot = ImageGrab.grab()
            target = get_dominant_color(screenshot)
            transition_steps = max(1, int(TRANSITION_TIME / SCREENSHOT_INTERVAL))
            r_step = (target[0] - current_color[0]) / transition_steps
            g_step = (target[1] - current_color[1]) / transition_steps
            b_step = (target[2] - current_color[2]) / transition_steps

            for _ in range(transition_steps):
                if stop_event.is_set():
                    break
                current_color[0] += r_step
                current_color[1] += g_step
                current_color[2] += b_step
                send_command({"globalColor": [int(current_color[0]), int(current_color[1]), int(current_color[2])]})
                time.sleep(SCREENSHOT_INTERVAL)
        except:
            pass

# --- Основной поток управления ---
def smooth_transition():
    global current_brightness, target_brightness
    global current_color, target_color, face_detected, screen_mode_active
    while running:
        if not face_detected:
            # Лампа выключена
            if current_color != [0, 0, 0] or current_brightness != 0:
                current_color = [0, 0, 0]
                current_brightness = 0
                send_command({"globalColor": [0, 0, 0], "brightness": 0})

        else:
            # Лицо есть
            if not screen_mode_active:
                # Восстановление целевых значений
                if current_brightness != target_brightness:
                    step = 1 if target_brightness > current_brightness else -1
                    current_brightness += step

                for i in range(3):
                    if current_color[i] != target_color[i]:
                        delta = target_color[i] - current_color[i]
                        step = 1 if delta > 0 else -1
                        current_color[i] += step

                send_command({"globalColor": current_color, "brightness": current_brightness})
            else:
                # В режиме "цвет с экрана" управление идет потоковым анализом
                pass
        time.sleep(0.01)

# --- Мониторинг лиц в кадре ---
def face_detection_loop():
    global face_detected, current_color, target_color
    cap = cv2.VideoCapture(0)
    while running:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.1)
            continue
        frame_with_faces, faceBoxes = highlightFace(faceNet, frame)
        face_detected = bool(faceBoxes)
        # Можно дополнительно реагировать на появление лица
        time.sleep(0.1)

# --- Мониторинг журнала Windows Event Log ---
def monitor_event_log():
    global last_total_events
    server = 'localhost'
    log_type = 'System'
    try:
        hand = win32evtlog.OpenEventLog(server, log_type)
        flags = win32evtlog.EVENTLOG_BACKWARDS_READ | win32evtlog.EVENTLOG_SEQUENTIAL_READ
        last_total_events = win32evtlog.GetNumberOfEventLogRecords(hand)
        print("Начинаю отслеживание событий. Ждите новых уведомлений...")
        while True:
            events = win32evtlog.ReadEventLog(hand, flags, 0)
            new_total = win32evtlog.GetNumberOfEventLogRecords(hand)
            if new_total > last_total_events:
                new_events_count = new_total - last_total_events
                start_idx = max(0, len(events) - new_events_count)
                new_events = events[start_idx:]
                print(f"Обнаружено {new_events_count} новых событий:")
                for event in new_events:
                    print(f"ID={event.EventID}, Source={event.SourceName}, Time={event.TimeGenerated}")
                    if notifications_enabled:
                        # Исправляем вызов flash_color
                        threading.Thread(target=flash_color, args=([0, 255, 0], 3, 0.3), daemon=True).start()
                last_total_events = new_total
            else:
                print("Нет новых событий.")
            time.sleep(5)
    except Exception as e:
        print(f"Ошибка при мониторинге журнала: {e}")

# --- Управление цветом и интерфейс ---
def choose_color():
    global target_color, current_color
    color_code = colorchooser.askcolor(title="Выберите цвет")
    if color_code and color_code[0]:
        # Проверяем, что цвет — это список из 3 элементов
        if isinstance(color_code[0], (list, tuple)) and len(color_code[0]) == 3:
            target_color = list(map(int, color_code[0]))
            current_color[:] = target_color
            send_command({"globalColor": target_color})
        else:
            messagebox.showerror("Ошибка", "Некорректный выбранный цвет")
    else:
        # Пользователь отменил выбор — ничего не делаем
        pass

def toggle_screen_color_mode():
    global screen_color_mode, screen_mode_active, screen_mode_stop_event, screen_mode_thread
    screen_color_mode = not screen_color_mode
    if screen_color_mode:
        # запуск анализа цвета
        screen_mode_active = True
        screen_mode_stop_event.clear()
        screen_mode_thread = threading.Thread(target=screen_color_analyzer, args=(screen_mode_stop_event,), daemon=True)
        screen_mode_thread.start()
        if screen_button:
            screen_button.config(text="Цвет с экрана: ВКЛ")
    else:
        # остановка анализа цвета
        screen_mode_active = False
        if screen_mode_stop_event:
            screen_mode_stop_event.set()
        if screen_button:
            screen_button.config(text="Цвет с экрана: ВЫКЛ")

def schedule_reminder():
    def set_reminder():
        try:
            rem_time_str = time_entry.get()
            rem_time = time.strptime(rem_time_str, "%H:%M")
            task = task_entry.get()
            color = colorchooser.askcolor(title="Цвет напоминания")
            if color and color[0]:
                rem_color = list(map(int, color[0]))
                threading.Thread(target=wait_and_trigger, args=(rem_time, task, rem_color), daemon=True).start()
                popup.destroy()
            else:
                messagebox.showerror("Ошибка", "Цвет не выбран")
        except:
            messagebox.showerror("Ошибка", "Некорректное время или данные")
           
    def wait_and_trigger(rem_time, task, color):
        while True:
            now = time.localtime()
            if (now.tm_hour == rem_time.tm_hour) and (now.tm_min == rem_time.tm_min):
                # Исправляем вызов flash_color
                threading.Thread(target=flash_color, args=(color, 5, 0.3), daemon=True).start()
                messagebox.showinfo("Напоминание", task)
                break
            time.sleep(30)

    popup = tk.Toplevel(root)
    popup.title("Запланировать напоминание")
    tk.Label(popup, text="Введите время (чч:мм):").pack(pady=5)
    time_entry = ttk.Entry(popup)
    time_entry.pack(pady=5)
    tk.Label(popup, text="Введите задание:").pack(pady=5)
    task_entry = ttk.Entry(popup)
    task_entry.pack(pady=5)
    ttk.Button(popup, text="Запланировать", command=set_reminder).pack(pady=10)

def toggle_notifications():
    global notifications_enabled
    notifications_enabled = not notifications_enabled
    label_text = "Включены" if notifications_enabled else "Выключены"
    notif_button.config(text=f"Уведомления на лампе: {label_text}")

def on_closing():
    global running
    running = False
    disconnect_from_esp()
    root.destroy()
    exit()

# --- Функция мигания лампой ---
def flash_color(color, flashes=3, flash_delay=1):
    global current_color
    if not isinstance(color, list) or len(color) != 3:
        print("Ошибка: color должен быть списком из трех элементов.")
        return
    try:
        original_color = current_color.copy()
    except:
        original_color = [0, 0, 0]
    for _ in range(flashes):
        send_command({"globalColor": color})
        time.sleep(flash_delay)
        send_command({"globalColor": [0, 0, 0]})
        time.sleep(flash_delay)
    send_command({"globalColor": original_color})

# --- Запуск ---
if __name__ == "__main__":
    if not connect_to_esp():
        print("Не удалось подключиться к ESP. Работа программы завершена.")
        exit()

    # Создаем GUI
    root = tk.Tk()
    root.title("AmbiLamp Control")

    screen_button = ttk.Button(root, text="Цвет с экрана: ВЫКЛ", command=toggle_screen_color_mode)
    screen_button.pack(pady=5)

    ttk.Button(root, text="Запланировать напоминание", command=schedule_reminder).pack(pady=5)
    ttk.Button(root, text="Выбрать цвет из палитры", command=choose_color).pack(pady=5)
    notif_button = ttk.Button(root, text="Уведомления на лампе: Включены", command=toggle_notifications)
    notif_button.pack(pady=5)

    root.protocol("WM_DELETE_WINDOW", on_closing)

    # Запуск потоков
    threading.Thread(target=smooth_transition, daemon=True).start()
    threading.Thread(target=monitor_event_log, daemon=True).start()
    threading.Thread(target=face_detection_loop, daemon=True).start()

    root.mainloop()