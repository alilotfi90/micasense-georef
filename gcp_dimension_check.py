import numpy as np
import matplotlib.pyplot as plt
from PIL import Image
import math

from micasense_georef_v2 import load_model, pixel_to_ground
#from micasense_georef_v2 import load_model, pixel_to_ground

def verify_gcp(image_path, ground_elev):
    print("Loading camera model and image...")
    # Load the metadata and camera parameters using the original code
    model = load_model(image_path)
    
    img = Image.open(image_path)
    img_array = np.array(img)
    
    # Create an interactive plot
    fig, ax = plt.subplots(figsize=(10, 10))
    ax.imshow(img_array, cmap='gray')
    ax.set_title("Click the 4 corners of the GCP (in order around the edge).\n"
                 "Scroll to zoom in/out at the cursor. Then close this window.")

    def on_scroll(event):
        # Zoom in/out about the cursor position
        if event.inaxes is not ax:
            return
        scale = 0.8 if event.button == "up" else 1.25
        x0, x1 = ax.get_xlim()
        y0, y1 = ax.get_ylim()
        ax.set_xlim(event.xdata + (x0 - event.xdata) * scale,
                    event.xdata + (x1 - event.xdata) * scale)
        ax.set_ylim(event.ydata + (y0 - event.ydata) * scale,
                    event.ydata + (y1 - event.ydata) * scale)
        fig.canvas.draw_idle()

    fig.canvas.mpl_connect("scroll_event", on_scroll)

    print("Waiting for you to click the 4 corners on the image...")
    # This pauses the script and waits for 4 mouse clicks
    clicks = plt.ginput(4, timeout=0) 
    plt.close()
    
    if len(clicks) < 4:
        print("Error: You didn't click 4 points!")
        return

    print("\n--- Raw Pixel Coordinates ---")
    world_coords = []
    for i, (u, v) in enumerate(clicks):
        print(f"Corner {i+1}: Pixel (X: {u:.1f}, Y: {v:.1f})")
        
        # converting the pixel to a real-world coordinate
        E, N = pixel_to_ground(model, u, v, ground_elev)
        world_coords.append((E, N))

    print("\n--- Real-World Coordinates (UTM Meters) ---")
    for i, (E, N) in enumerate(world_coords):
        print(f"Corner {i+1}: Easting {E:.3f}, Northing {N:.3f}")

    print("\n--- Calculated Dimensions ---")
    print("Target dimension is roughly 0.600 meters (60cm).\n")
    
    # Calculate the distance between the points (1->2, 2->3, 3->4, 4->1)
    for i in range(4):
        # Grab the current corner and the next corner (wrapping back to 0 at the end)
        p1 = world_coords[i]
        p2 = world_coords[(i + 1) % 4] 
        
        # math.hypot automatically calculates the Euclidean distance formula
        distance = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        
        print(f"Distance from Corner {i+1} to {(i+1)%4 + 1}: {distance:.3f} meters")

if __name__ == "__main__":
    IMAGE_FILE = "/home/nzy764/dev/more sample images/IMG_0505_1.tif"
    #IMAGE_FILE = "/home/nzy764/raw images/kernan2025/20250630/Raw/001/IMG_0202_1.tif"

    # Orthometric (MSL) ground elevation at the GCP.  481.5 m was Saskatoon's
    # nominal city elevation; the field at this frame's lat/lon is ~512 m MSL
    # (SRTM says ~508 m; 512 m back-solved from the 60 cm GCP).
    #GROUND_ELEVATION = 512.0
    
    AGL = 12.0
    model = load_model(IMAGE_FILE)
    
    verify_gcp(IMAGE_FILE, model["alt"] - AGL)