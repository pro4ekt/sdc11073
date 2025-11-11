from sense_hat import SenseHat
import asyncio
import threading
import time
import os

OFFSET_LEFT = 1
OFFSET_TOP = 2

sense = SenseHat()
show_temp = True

NUMS =[1,1,1,1,0,1,1,0,1,1,0,1,1,1,1,  # 0
       0,1,0,0,1,0,0,1,0,0,1,0,0,1,0,  # 1
       1,1,1,0,0,1,0,1,0,1,0,0,1,1,1,  # 2
       1,1,1,0,0,1,1,1,1,0,0,1,1,1,1,  # 3
       1,0,0,1,0,1,1,1,1,0,0,1,0,0,1,  # 4
       1,1,1,1,0,0,1,1,1,0,0,1,1,1,1,  # 5
       1,1,1,1,0,0,1,1,1,1,0,1,1,1,1,  # 6
       1,1,1,0,0,1,0,1,0,1,0,0,1,0,0,  # 7
       1,1,1,1,0,1,1,1,1,1,0,1,1,1,1,  # 8
       1,1,1,1,0,1,1,1,1,0,0,1,0,0,1]  # 9

# Displays a single digit (0-9)
def show_digit(val, xd, yd, r, g, b):
  offset = val * 15
  for p in range(offset, offset + 15):
    xt = p % 3
    yt = (p-offset) // 3
    sense.set_pixel(xt+xd, yt+yd, r*NUMS[p], g*NUMS[p], b*NUMS[p])

# Displays a two-digits positive number (0-99)
def show_number(val, r, g, b):
  abs_val = abs(val)
  tens = abs_val // 10
  units = abs_val % 10
  if (abs_val > 9): show_digit(tens, OFFSET_LEFT, OFFSET_TOP, r, g, b)
  show_digit(units, OFFSET_LEFT+4, OFFSET_TOP, r, g, b)

def joystick():
    global show_temp
    while True:
        events = sense.stick.get_events()
        for e in events:
         if e.action == "pressed":
             show_temp = not show_temp
             start = time.time()
         if e.action == "held":
             duration = time.time() - start
             if(duration > 3):
              os.execvp("python3", ["python3", "Pi5 CPU Temp + Fans Control/provider2.py"])
        time.sleep(0.05)

def display():
    global show_temp
    while True:
       #print(show_temp)
        if show_temp:
         temp()        
        else:
         hum()
        time.sleep(0.05)

def temp():
    sense.clear()
        
    temperature = sense.temperature

    show_number(int(temperature), 255, 255, 255)

    time.sleep(1)

def hum():
    sense.clear()
        
    humidity = sense.humidity

    show_number(int(humidity), 255, 255, 255)

    time.sleep(1)

if __name__ == '__main__':
    t = threading.Thread(target=joystick, daemon=True)
    t.start()
    display()