import AsyncStorage from '@react-native-async-storage/async-storage';
import { fetch as expoFetch } from 'expo/fetch';
import { File } from 'expo-file-system';
import { Platform } from 'react-native';

export const DEFAULT_API_URL = process.env.EXPO_PUBLIC_API_URL || 'http://localhost:8000';
const TOKEN_KEY = 'facelock_token';
const API_URL_KEY = 'facelock_api_url';

export type User = { id: number; email: string; display_name: string; alerts_enabled: boolean };
export type Activity = { id: number; label: string; confidence: number; seen_at: string; image_url?: string; device_id?: number; device_name?: string };
export type Person = { id: number; name: string; notes?: string; face_count: number; created_at: string; appearances: number; last_seen?: string; recent_activities: Activity[] };
export type Unknown = { id: number; label: string; first_seen: string; last_seen: string; sightings: number; image_url: string; activities: Activity[] };
export type Device = { id: number; name: string; recording_enabled: boolean; online: boolean; created_at: string; last_seen?: string };
export type PairingSession = { token: string; qr_payload: string; expires_at: string };
export type PairingStatus = { paired: boolean; expires_at: string; device?: Device };
export type Recording = { id: number; device_id: number; device_name: string; started_at: string; ended_at: string; frame_count: number; video_url: string };
export type Detection = { kind: 'known' | 'unknown' | 'pending'; label: string; confidence: number; box: number[]; unknown_id?: number; alert_created: boolean };
export type LiveFeed = { online: boolean; frame_data_url?: string; detections: Detection[]; captured_at?: string; source_session_id?: string; device_id?: number; device_name?: string };

export async function token() { return AsyncStorage.getItem(TOKEN_KEY); }
export async function getApiUrl() { return (await AsyncStorage.getItem(API_URL_KEY)) || DEFAULT_API_URL; }
export async function setApiUrl(value: string) {
  const normalized = value.trim().replace(/\/$/, '');
  await AsyncStorage.setItem(API_URL_KEY, normalized || DEFAULT_API_URL);
}
export async function setToken(value: string | null) {
  if (value) await AsyncStorage.setItem(TOKEN_KEY, value); else await AsyncStorage.removeItem(TOKEN_KEY);
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const auth = await token();
  const apiUrl = await getApiUrl();
  const headers = new Headers(options.headers);
  if (auth) headers.set('Authorization', `Bearer ${auth}`);
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  const response = await expoFetch(`${apiUrl}${path}`, { ...options, headers });
  if (!response.ok) {
    const value = await response.json().catch(() => ({}));
    throw new Error(value.detail || `Request failed (${response.status})`);
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

export async function uriToFormFile(form: FormData, field: string, uri: string, name: string) {
  if (Platform.OS === 'web') {
    const blob = await (await fetch(uri)).blob();
    form.append(field, blob, name);
  } else {
    // Expo SDK 57 no longer accepts React Native's legacy { uri, name, type }
    // object as a multipart part. File implements the Blob interface expected by
    // expo/fetch and works with both file:// and Android content:// URIs.
    form.append(field, new File(uri));
  }
}
