import math
import sys
import json
from pyargus.directionEstimation import *
from scipy.spatial import distance
from collections import deque
import numpy as np

N_SAMPLES_OF_REF_PERIOD = 8
NUMBER_MESSAGES = 1 # data accumulation window
SIGMA_FILTER_WINDOW = 5

# map parameters
width_map = 14.7
height_map = 12.4
kx_locator = 0.532471
ky_locator = 0.558504
x_locator = kx_locator * width_map
y_locator = ky_locator * height_map
z_locator = 0.2
z_beacon = 0.814

# antenna array parameters
frequency = 2480
wavelength = 0.12
d = 0.05  # inter element spacing
M = 2  # number of antenna elements in the antenna system


# 68–95–99.7 rule for identify and discard positional outliers 
class SigmaFilter:
    def __init__(self, maxlen = SIGMA_FILTER_WINDOW):
        self.deque = deque(maxlen = maxlen)

    def isValid(self, v):
        isValid = False
        if len(self.deque) == SIGMA_FILTER_WINDOW:
            mean_v, std_v = np.array(self.deque).mean(axis=0), np.array(self.deque).std(axis=0)
            if distance.euclidean(v, mean_v) < distance.euclidean(mean_v + 3 * std_v, mean_v):
                isValid = True
            else:
                print('outlier:', v)

        self.deque.append(v)
        return isValid


def to_plus_minus_pi(angle):
    while angle >= 180:
        angle -= 2 * 180
    while angle < -180:
        angle += 2 * 180
    return angle


def get_angle(X):
    # Estimating the spatial correlation matrix
    R = corr_matrix_estimate(X.T, imp="fast")

    array_alignment = np.arange(0, M, 1) * d
    incident_angles = np.arange(-90, 91, 1)
    scanning_vectors = np.zeros((M, np.size(incident_angles)), dtype=complex)
    for i in range(np.size(incident_angles)):
        scanning_vectors[:, i] = np.exp(
            array_alignment * 1j * 2 * np.pi * np.sin(np.radians(incident_angles[i])) / wavelength)  # scanning vector

    ula_scanning_vectors = scanning_vectors

    # Estimate DOA
    MUSIC = DOA_MUSIC(R, ula_scanning_vectors, signal_dimension=1)
    norm_data = np.divide(np.abs(MUSIC), np.max(np.abs(MUSIC)))
    return float(incident_angles[np.where(norm_data == 1)[0]][0])


def get_coordinate(azimuth, elevation, height, receiver_coords):
    nx = np.cos(np.deg2rad(90.0 - azimuth))
    nz = np.cos(np.deg2rad(90.0 - abs(elevation)))
    if math.isclose(nx, 0.0, abs_tol=1e-16) or math.isclose(nz, 0.0, abs_tol=1e-16):
        return [float("nan"),  float("nan")]
    else:
        ny = np.sqrt(1 - nx ** 2 - nz ** 2)
        t = (height - receiver_coords[2]) / nz
        x = receiver_coords[0] + t * nx
        y = receiver_coords[1] - t * ny
    return [x, y]


if __name__ == '__main__':
    velSigmaFilter = SigmaFilter(SIGMA_FILTER_WINDOW)
    messages = []
    for line in sys.stdin:
        data = json.loads(line)
        try:
            aoa_data = json.loads(data['message'])
        except BaseException as e:
            # print(e)
            continue

        try:
            for i in aoa_data[1:]:
                if i['aoa']['frequency'] == frequency:
                    messages.append(i)
        except BaseException as e:
            # print(e)
            pass

        if len(messages) < NUMBER_MESSAGES:
            continue

        x_00, azimuth_x_12, elevation_x_12 = [], [], []
        azimuth_phases, elevation_phases = [], []
        for i in messages:
            ref_phases = []
            iq_samples = [i['aoa']['iq'][n:n + 2] for n in range(0, len(i['aoa']['iq']), 2)]

            for iq_idx in range(N_SAMPLES_OF_REF_PERIOD - 1):
                iq_next = complex(iq_samples[iq_idx + 1][0], iq_samples[iq_idx + 1][1])
                iq_cur = complex(iq_samples[iq_idx][0], iq_samples[iq_idx][1])
                phase_next = np.rad2deg(np.arctan2(iq_next.imag, iq_next.real))
                phase_cur = np.rad2deg(np.arctan2(iq_cur.imag, iq_cur.real))
                ref_phases.append((to_plus_minus_pi(phase_next - phase_cur)))
            phase_ref = np.mean(ref_phases)

            iq_2ant_batches = [iq_samples[n:n + 2] for n in range(N_SAMPLES_OF_REF_PERIOD, len(iq_samples), 2)]
            for iq_batch_idx, iq_batch in enumerate(iq_2ant_batches[:-1]):
                iq_next = complex(iq_batch[1][0], iq_batch[1][1])
                iq_cur = complex(iq_batch[0][0], iq_batch[0][1])
                phase_next = np.rad2deg(np.arctan2(iq_next.imag, iq_next.real))
                phase_cur = np.rad2deg(np.arctan2(iq_cur.imag, iq_cur.real))
                diff_phase = to_plus_minus_pi((phase_next - phase_cur) - 2 * phase_ref)
                if iq_batch_idx % 2 != 0:
                    elevation_phases.append(diff_phase)
                else:
                    x_00.append(1)
                    azimuth_phases.append(diff_phase)
        messages.clear()
        # MUSIC algo
        X = np.zeros((M, np.size(x_00)), dtype=complex)
        X[0, :] = x_00
        for i in azimuth_phases:
            azimuth_x_12.append(np.exp(1j * np.deg2rad(i)))
        X[1, :] = azimuth_x_12
        azimuth_angle = get_angle(X)

        for i in elevation_phases:
            elevation_x_12.append(np.exp(1j * np.deg2rad(i)))
        X[1, :] = elevation_x_12
        elevation_angle = get_angle(X)
        print(f'azimuth_angle:{azimuth_angle}, elevation_angle:{elevation_angle}')

        xy = get_coordinate(azimuth_angle, elevation_angle, z_beacon, [x_locator, y_locator, z_locator])
        if not math.isnan(xy[0]) and not math.isnan(xy[1]):
            if velSigmaFilter.isValid(xy):
                print(f'x_beacon:{xy[0]}, y_beacon:{xy[1]}')
