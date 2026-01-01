import ctypes
import time
from ctypes import wintypes

# CLSIDs / IIDs from Windows Location API
CLSID_Location = ctypes.GUID("{E5B8E079-EE6D-4E33-A438-C87F2E959254}")
IID_ILocation  = ctypes.GUID("{AB2ECE69-56D9-4F28-B525-DE1B0EE44237}")
IID_ILocationReport = ctypes.GUID("{C8B7F7EE-75D0-4DB9-B62D-7A0F369CA456}")

# LocationReportType_LatLong = 0
REPORT_TYPE_LATLONG = 0

# Accuracy flags
LOCATION_DESIRED_ACCURACY_HIGH = 1

# Initialize COM
ctypes.windll.ole32.CoInitialize(None)

class ILocation(ctypes.c_void_p):
    pass

class ILocationReport(ctypes.c_void_p):
    pass

def get_lat_lon():
    loc = ILocation()

    hr = ctypes.windll.ole32.CoCreateInstance(
        ctypes.byref(CLSID_Location),
        None,
        1,  # CLSCTX_INPROC_SERVER
        ctypes.byref(IID_ILocation),
        ctypes.byref(loc)
    )
    if hr != 0:
        raise RuntimeError(f"CoCreateInstance failed: 0x{hr:08X}")

    # Request permissions implicitly (Windows handles dialog/policy)
    ctypes.windll.ole32.CoInitialize(None)

    # Get report interface
    report = ILocationReport()
    vtbl = ctypes.cast(loc, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))

    # ILocation::GetReport
    GetReport = ctypes.CFUNCTYPE(
        ctypes.c_long,
        ctypes.c_int,
        ctypes.POINTER(ILocationReport)
    )(vtbl[0][3])  # method index

    # Wait for GPS fix
    print("Waiting for GPS fix...")
    start = time.time()
    while time.time() - start < 15:
        hr = GetReport(REPORT_TYPE_LATLONG, ctypes.byref(report))
        if hr == 0 and report:
            break
        time.sleep(1)

    if not report:
        raise RuntimeError("No GPS fix")

    rtbl = ctypes.cast(report, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))

    # ILocationReport::GetLatitude / GetLongitude
    GetLatitude = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.POINTER(ctypes.c_double))(rtbl[0][6])
    GetLongitude = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.POINTER(ctypes.c_double))(rtbl[0][7])

    lat = ctypes.c_double()
    lon = ctypes.c_double()

    GetLatitude(ctypes.byref(lat))
    GetLongitude(ctypes.byref(lon))

    return lat.value, lon.value

if __name__ == "__main__":
    try:
        lat, lon = get_lat_lon()
        print("✅ GPS FIX")
        print(f"Latitude : {lat:.6f}")
        print(f"Longitude: {lon:.6f}")
    except Exception as e:
        print("❌ GPS ERROR:", e)
