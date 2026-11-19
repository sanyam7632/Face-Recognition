import { Ionicons } from '@expo/vector-icons';
import { BarcodeScanningResult, CameraView, useCameraPermissions } from 'expo-camera';
import * as ImagePicker from 'expo-image-picker';
import { StatusBar } from 'expo-status-bar';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import QRCode from 'react-native-qrcode-svg';
import {
  ActivityIndicator, Alert, Image, Linking, Modal, Platform, Pressable, SafeAreaView,
  ScrollView, StyleSheet, Switch, Text, TextInput, useWindowDimensions, View
} from 'react-native';
import {
  Activity, api, DEFAULT_API_URL, Detection, Device, getApiUrl, LiveFeed, Person,
  PairingSession, PairingStatus, Recording, setApiUrl, setToken, token, Unknown,
  uriToFormFile, User
} from './src/api';
import { colors as c } from './src/theme';

type Tab = 'home' | 'live' | 'people' | 'unknowns' | 'recordings' | 'settings';
const STREAM_INTERVAL_MS = 67;
const STREAM_JPEG_QUALITY = .65;

const when = (value?: string) => value ? new Date(value).toLocaleString() : 'Never';

const Button = ({ title, onPress, secondary = false, danger = false, disabled = false }: {
  title: string; onPress: () => void; secondary?: boolean; danger?: boolean; disabled?: boolean;
}) => <Pressable onPress={onPress} disabled={disabled} style={({ pressed }) => [
  styles.button, secondary && styles.buttonSecondary, danger && styles.buttonDanger,
  (pressed || disabled) && { opacity: .6 }
]}><Text style={[styles.buttonText, secondary && { color: c.text }]}>{title}</Text></Pressable>;

function Auth({ onDone }: { onDone: (user: User) => void }) {
  const [register, setRegister] = useState(false);
  const [name, setName] = useState(''); const [email, setEmail] = useState(''); const [password, setPassword] = useState('');
  const [serverUrl, setServerUrl] = useState(DEFAULT_API_URL);
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  useEffect(() => { getApiUrl().then(setServerUrl); }, []);
  const submit = async () => {
    setBusy(true); setError('');
    try {
      await setApiUrl(serverUrl);
      const result = await api<{ access_token: string; user: User }>(register ? '/auth/register' : '/auth/login', {
        method: 'POST', body: JSON.stringify(register ? { display_name: name, email, password } : { email, password })
      });
      await setToken(result.access_token); onDone(result.user);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  return <SafeAreaView style={styles.authPage}><StatusBar style="dark" />
    <View style={styles.authCard}>
      <View style={styles.logo}><Ionicons name="scan" size={30} color={c.white} /></View>
      <Text style={styles.brand}>FACE RECOGNITION</Text>
      <Text style={styles.authTitle}>{register ? 'Create your CCTV space' : 'Welcome back'}</Text>
      <Text style={styles.sub}>Monitor your cameras, people, alerts, and recordings.</Text>
      {register && <TextInput value={name} onChangeText={setName} placeholder="Your name" placeholderTextColor={c.muted} style={styles.input} />}
      <TextInput value={email} onChangeText={setEmail} autoCapitalize="none" keyboardType="email-address" placeholder="Email" placeholderTextColor={c.muted} style={styles.input} />
      <TextInput value={password} onChangeText={setPassword} secureTextEntry placeholder="Password (8+ characters)" placeholderTextColor={c.muted} style={styles.input} />
      <TextInput value={serverUrl} onChangeText={setServerUrl} autoCapitalize="none" autoCorrect={false} keyboardType="url" placeholder="Backend URL" placeholderTextColor={c.muted} style={styles.input} />
      {!!error && <Text style={styles.error}>{error}</Text>}
      <Button title={busy ? 'Please wait…' : register ? 'Create account' : 'Sign in'} onPress={submit} disabled={busy} />
      <Pressable onPress={() => setRegister(!register)}><Text style={styles.link}>{register ? 'Already registered? Sign in' : 'New here? Create an account'}</Text></Pressable>
      <Text style={styles.privacy}>Only monitor people and places where you have permission.</Text>
    </View>
  </SafeAreaView>;
}

function Empty({ icon, text }: { icon: keyof typeof Ionicons.glyphMap; text: string }) {
  return <View style={styles.empty}><Ionicons name={icon} size={30} color={c.muted} /><Text style={styles.muted}>{text}</Text></View>;
}

function Stat({ number, label }: { number: string; label: string }) {
  return <View style={styles.stat}><Text style={styles.statNum}>{number}</Text><Text style={styles.statLabel}>{label}</Text></View>;
}

function pairingToken(value: string): string | null {
  try {
    const payload = JSON.parse(value);
    return payload?.type === 'face-recognition-device-pair' && typeof payload.token === 'string' ? payload.token : null;
  } catch { return null; }
}

function PairDeviceModal({ visible, onClose, reload }: { visible: boolean; onClose: () => void; reload: () => Promise<void> }) {
  const [permission, requestPermission] = useCameraPermissions();
  const [session, setSession] = useState<PairingSession | null>(null); const [paired, setPaired] = useState<Device | null>(null);
  const [scannedToken, setScannedToken] = useState(''); const [name, setName] = useState(''); const [scanLocked, setScanLocked] = useState(false);
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const createQr = async () => {
    setBusy(true); setError(''); setPaired(null);
    try { setSession(await api<PairingSession>('/devices/pairing-sessions', { method: 'POST' })); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  useEffect(() => {
    if (!visible) return;
    setScannedToken(''); setName(''); setError(''); setScanLocked(false); setPaired(null);
    if (Platform.OS === 'web') createQr();
  }, [visible]);
  useEffect(() => {
    if (!visible || !session || paired || Platform.OS !== 'web') return;
    let active = true;
    const check = async () => {
      try {
        const status = await api<PairingStatus>(`/devices/pairing-sessions/${session.token}`);
        if (active && status.paired && status.device) { setPaired(status.device); await reload(); }
      } catch (e) { if (active) setError((e as Error).message); }
    };
    const timer = setInterval(check, 1500); check();
    return () => { active = false; clearInterval(timer); };
  }, [visible, session, paired, reload]);
  const scan = ({ data }: BarcodeScanningResult) => {
    if (scanLocked || scannedToken) return;
    setScanLocked(true);
    const value = pairingToken(data);
    if (!value) {
      Alert.alert('Not a Face Recognition code', 'Scan the pairing QR shown on the laptop website.');
      setTimeout(() => setScanLocked(false), 1200);
      return;
    }
    setScannedToken(value); setScanLocked(false);
  };
  const finishPairing = async () => {
    if (!name.trim() || !scannedToken) return;
    setBusy(true); setError('');
    try {
      const device = await api<Device>('/devices/pair', { method: 'POST', body: JSON.stringify({ token: scannedToken, name: name.trim() }) });
      await reload(); onClose(); Alert.alert('Camera connected', `${device.name} is now available in Live Feed.`);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  return <Modal visible={visible} animationType="slide" onRequestClose={onClose}>
    <SafeAreaView style={styles.pairPage}><StatusBar style="dark" />
      <View style={styles.pairTop}><Pressable onPress={onClose} style={styles.closeButton}><Ionicons name="close" size={28} color={c.text} /></Pressable><Text style={styles.pairTitle}>{Platform.OS === 'web' ? 'Pair a camera' : 'Scan laptop QR'}</Text></View>
      {Platform.OS === 'web' ? <View style={styles.pairBody}>
        <Text style={styles.hero}>Connect your phone</Text><Text style={[styles.sub, { textAlign: 'center' }]}>On Android, open Add camera and scan this one-time code. Use the same signed-in account on both devices.</Text>
        {busy && !session ? <ActivityIndicator size="large" color={c.mint} /> : paired ? <View style={styles.pairSuccess}><Ionicons name="checkmark-circle" size={70} color={c.mint} /><Text style={styles.cardTitle}>{paired.name} connected</Text><Button title="Done" onPress={onClose} /></View> : session ? <View style={styles.qrCard}><View style={styles.qrFrame}><QRCode value={session.qr_payload} size={230} color="#10261c" backgroundColor="#ffffff" /></View><Text style={styles.muted}>Expires {when(session.expires_at)}</Text></View> : null}
        {!!error && <><Text style={styles.error}>{error}</Text><Button title="Generate a new QR code" onPress={createQr} secondary disabled={busy} /></>}
      </View> : <View style={styles.pairBody}>
        {!scannedToken ? !permission ? <ActivityIndicator size="large" color={c.mint} /> : !permission.granted ? <View style={styles.permissionCard}><Ionicons name="camera-outline" size={52} color={c.mint} /><Text style={styles.cardTitle}>Camera permission needed</Text><Text style={[styles.muted, { textAlign: 'center' }]}>Camera access is used only to scan the pairing QR.</Text><Button title="Allow camera" onPress={requestPermission} /></View> : <View style={styles.scannerFrame}><CameraView style={styles.scanner} facing="back" barcodeScannerSettings={{ barcodeTypes: ['qr'] }} onBarcodeScanned={scan} /><View pointerEvents="none" style={styles.scanGuide} /><Text style={styles.scanHint}>Point the camera at the QR shown on your laptop</Text></View> : <View style={styles.nameDeviceCard}><Ionicons name="checkmark-circle" size={56} color={c.mint} /><Text style={styles.cardTitle}>QR code accepted</Text><Text style={[styles.muted, { textAlign: 'center' }]}>Give this laptop camera a name so you can identify its feed.</Text><TextInput value={name} onChangeText={setName} autoFocus placeholder="Camera name, e.g. Office Laptop" placeholderTextColor={c.muted} style={[styles.input, { width: '100%' }]} /><Button title={busy ? 'Connecting…' : 'Connect camera'} onPress={finishPairing} disabled={busy || !name.trim()} /><Button title="Scan again" onPress={() => { setScannedToken(''); setScanLocked(false); }} secondary disabled={busy} />{!!error && <Text style={styles.error}>{error}</Text>}</View>}
      </View>}
    </SafeAreaView>
  </Modal>;
}

function AddDevice({ reload, compact = false }: { reload: () => Promise<void>; compact?: boolean }) {
  const [visible, setVisible] = useState(false);
  return <View style={[styles.addDevice, compact && { marginTop: 8 }]}>
    <Pressable onPress={() => setVisible(true)} style={({ pressed }) => [styles.pairButton, pressed && { opacity: .7 }]}>
      <Ionicons name={Platform.OS === 'web' ? 'qr-code-outline' : 'scan-outline'} size={21} color={c.white} /><Text style={styles.smallGreenText}>{Platform.OS === 'web' ? 'Show pairing QR' : 'Scan laptop QR'}</Text>
    </Pressable>
    <PairDeviceModal visible={visible} onClose={() => setVisible(false)} reload={reload} />
  </View>;
}

function DeviceRow({ device, reload }: { device: Device; reload: () => Promise<void> }) {
  const [deleting, setDeleting] = useState(false);
  const remove = () => Alert.alert(`Remove ${device.name}?`, 'Its saved recordings will be deleted. Face activity history will be kept.', [
    { text: 'Cancel', style: 'cancel' }, { text: 'Remove', style: 'destructive', onPress: async () => {
      setDeleting(true);
      try { await api(`/devices/${device.id}`, { method: 'DELETE' }); await reload(); }
      catch (e) { Alert.alert('Could not remove device', (e as Error).message); } finally { setDeleting(false); }
    } }
  ]);
  return <View style={styles.deviceRow}>
    <View style={[styles.deviceIcon, device.online && styles.deviceIconOnline]}><Ionicons name="videocam-outline" size={21} color={device.online ? c.white : c.muted} /></View>
    <View style={{ flex: 1 }}><Text style={styles.rowTitle}>{device.name}</Text><Text style={styles.muted}>{device.online ? 'Live feed connected' : `Offline · Last seen ${when(device.last_seen)}`}</Text></View>
    {device.recording_enabled && <View style={styles.recordingBadge}><View style={styles.recordingDot} /><Text style={styles.recordingBadgeText}>REC</Text></View>}
    <Pressable onPress={remove} disabled={deleting} style={styles.removeDeviceButton}>{deleting ? <ActivityIndicator size="small" color={c.danger} /> : <Ionicons name="trash-outline" size={19} color={c.danger} />}</Pressable>
  </View>;
}

function Home({ user, people, unknowns, devices, go, reload }: {
  user: User; people: Person[]; unknowns: Unknown[]; devices: Device[]; go: (tab: Tab) => void; reload: () => Promise<void>;
}) {
  const online = devices.filter(device => device.online).length;
  return <ScrollView contentContainerStyle={styles.content}>
    <Text style={styles.eyebrow}>OVERVIEW</Text><Text style={styles.hero}>Good day, {user.display_name.split(' ')[0]}.</Text>
    <Text style={styles.sub}>Your connected CCTV devices and recognition activity.</Text>
    <Pressable style={styles.monitorCard} onPress={() => go('live')}>
      <View style={{ flex: 1 }}><View style={styles.live}><View style={styles.dot} /><Text style={styles.liveText}>{online ? `${online} DEVICE${online > 1 ? 'S' : ''} LIVE` : 'WAITING FOR A CAMERA'}</Text></View>
        <Text style={styles.cardTitle}>{online ? devices.filter(device => device.online).map(device => device.name).join(', ') : 'Open Live Feed'}</Text>
        <Text style={styles.muted}>Watch one camera or combine feeds from multiple devices.</Text></View>
      <View style={styles.roundIcon}><Ionicons name="arrow-forward" size={23} color={c.white} /></View>
    </Pressable>
    <View style={styles.stats}><Stat number={String(devices.length)} label="Devices" /><Stat number={String(people.length)} label="Known people" /><Stat number={String(unknowns.length)} label="Unknown people" /></View>
    <View style={styles.sectionHeader}><Text style={styles.sectionTitle}>Camera devices</Text><Pressable onPress={() => go('recordings')}><Text style={styles.linkInline}>Recording controls</Text></Pressable></View>
    {devices.map(device => <DeviceRow key={device.id} device={device} reload={reload} />)}
    <AddDevice reload={reload} compact />
    <Text style={styles.sectionTitle}>Recent unknown activity</Text>
    {unknowns.slice(0, 3).map(item => <View key={item.id} style={styles.row}><Image source={{ uri: item.image_url }} style={styles.avatar} /><View style={{ flex: 1 }}><Text style={styles.rowTitle}>{item.label}</Text><Text style={styles.muted}>{item.sightings} appearance{item.sightings === 1 ? '' : 's'} · {when(item.last_seen)}</Text></View></View>)}
    {!unknowns.length && <Empty icon="shield-checkmark-outline" text="No unknown people recorded yet." />}
  </ScrollView>;
}

function ActivityLine({ item }: { item: Activity }) {
  return <View style={styles.activityLine}>
    <Ionicons name="time-outline" size={15} color={c.mint} />
    <Text style={styles.activityText}>{when(item.seen_at)}{item.device_name ? ` · ${item.device_name}` : ''}</Text>
  </View>;
}

function People({ items, reload }: { items: Person[]; reload: () => Promise<void> }) {
  const [name, setName] = useState(''); const [photos, setPhotos] = useState<ImagePicker.ImagePickerAsset[]>([]);
  const [busy, setBusy] = useState(false); const [addingTo, setAddingTo] = useState<number | null>(null); const [deleting, setDeleting] = useState<number | null>(null);
  const pick = async () => { const result = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ['images'], allowsMultipleSelection: true, quality: .85, selectionLimit: 8 }); if (!result.canceled) setPhotos(result.assets); };
  const save = async () => {
    if (!name.trim() || !photos.length) return Alert.alert('Add a name and at least one photo.');
    setBusy(true);
    try {
      const form = new FormData(); form.append('name', name.trim());
      for (let i = 0; i < photos.length; i++) await uriToFormFile(form, 'photos', photos[i]!.uri, `face-${i}.jpg`);
      await api('/people', { method: 'POST', body: form }); setName(''); setPhotos([]); await reload();
    } catch (e) { Alert.alert('Could not learn face', (e as Error).message); } finally { setBusy(false); }
  };
  const addAngles = async (person: Person) => {
    const selected = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ['images'], allowsMultipleSelection: true, quality: .85, selectionLimit: 8 });
    if (selected.canceled) return;
    setAddingTo(person.id);
    try {
      const form = new FormData();
      for (let i = 0; i < selected.assets.length; i++) await uriToFormFile(form, 'photos', selected.assets[i]!.uri, `angle-${i}.jpg`);
      await api(`/people/${person.id}/photos`, { method: 'POST', body: form }); await reload();
    } catch (e) { Alert.alert('Could not add photos', (e as Error).message); } finally { setAddingTo(null); }
  };
  const remove = (person: Person) => Alert.alert(`Delete ${person.name}?`, 'This removes the person, reference photos, and activity history.', [
    { text: 'Cancel', style: 'cancel' }, { text: 'Delete', style: 'destructive', onPress: async () => {
      setDeleting(person.id); try { await api(`/people/${person.id}`, { method: 'DELETE' }); await reload(); }
      catch (e) { Alert.alert('Could not delete person', (e as Error).message); } finally { setDeleting(null); }
    } }
  ]);
  return <ScrollView contentContainerStyle={styles.content}>
    <Text style={styles.eyebrow}>PEOPLE</Text><Text style={styles.hero}>Known people</Text><Text style={styles.sub}>Reference photos, appearance counts, and recent feed activity.</Text>
    <View style={styles.formCard}><TextInput value={name} onChangeText={setName} placeholder="Person’s name" placeholderTextColor={c.muted} style={styles.input} />
      <Button title={photos.length ? `${photos.length} photo${photos.length > 1 ? 's' : ''} selected` : 'Choose reference photos'} onPress={pick} secondary />
      <Button title={busy ? 'Learning…' : 'Add known person'} onPress={save} disabled={busy} /></View>
    {items.map(person => <View key={person.id} style={styles.personCard}>
      <View style={styles.personTop}><View style={styles.initial}><Text style={styles.initialText}>{person.name[0]?.toUpperCase()}</Text></View>
        <View style={{ flex: 1 }}><Text style={styles.rowTitle}>{person.name}</Text><Text style={styles.muted}>{person.face_count} reference photo{person.face_count === 1 ? '' : 's'} · {person.appearances} appearance{person.appearances === 1 ? '' : 's'}</Text><Text style={styles.muted}>Last seen: {when(person.last_seen)}</Text></View>
        <View style={styles.personActions}><Pressable disabled={addingTo !== null || deleting !== null} onPress={() => addAngles(person)} style={styles.iconButtonGreen}>{addingTo === person.id ? <ActivityIndicator size="small" color={c.white} /> : <Ionicons name="images-outline" color={c.white} size={19} />}</Pressable><Pressable disabled={addingTo !== null || deleting !== null} onPress={() => remove(person)} style={styles.iconButtonDanger}>{deleting === person.id ? <ActivityIndicator size="small" color={c.danger} /> : <Ionicons name="trash-outline" color={c.danger} size={20} />}</Pressable></View>
      </View>
      {!!person.recent_activities.length && <View style={styles.activityBlock}><Text style={styles.activityHeading}>Recent activity</Text>{person.recent_activities.slice(0, 4).map(activity => <ActivityLine key={activity.id} item={activity} />)}</View>}
    </View>)}
    {!items.length && <Empty icon="people-outline" text="Add your first known person above." />}
  </ScrollView>;
}

function UnknownReview({ item, visible, onClose, reload }: { item: Unknown | null; visible: boolean; onClose: () => void; reload: () => Promise<void> }) {
  const [snapshot, setSnapshot] = useState(''); const [tag, setTag] = useState(''); const [personName, setPersonName] = useState(''); const [busy, setBusy] = useState(false);
  useEffect(() => { if (item) { setSnapshot(item.image_url); setTag(item.label); setPersonName(''); } }, [item]);
  if (!item) return null;
  const saveTag = async () => {
    if (!tag.trim()) return; setBusy(true);
    try { const form = new FormData(); form.append('label', tag.trim()); await api(`/unknowns/${item.id}/label`, { method: 'PATCH', body: form }); await reload(); }
    catch (e) { Alert.alert('Could not update tag', (e as Error).message); } finally { setBusy(false); }
  };
  const promote = async () => {
    if (!personName.trim()) return Alert.alert('Enter the person’s name.'); setBusy(true);
    try { const form = new FormData(); form.append('name', personName.trim()); await api(`/unknowns/${item.id}/promote`, { method: 'POST', body: form }); await reload(); onClose(); }
    catch (e) { Alert.alert('Could not add person', (e as Error).message); } finally { setBusy(false); }
  };
  const remove = () => Alert.alert(`Delete ${item.label}?`, 'This removes the unknown person and all saved appearance snapshots.', [
    { text: 'Cancel', style: 'cancel' }, { text: 'Delete', style: 'destructive', onPress: async () => {
      setBusy(true); try { await api(`/unknowns/${item.id}`, { method: 'DELETE' }); await reload(); onClose(); }
      catch (e) { Alert.alert('Could not delete unknown', (e as Error).message); } finally { setBusy(false); }
    } }
  ]);
  const snapshots = [item.image_url, ...item.activities.map(activity => activity.image_url).filter((value): value is string => Boolean(value))].filter((value, index, values) => values.indexOf(value) === index);
  return <Modal visible={visible} animationType="slide" onRequestClose={onClose}>
    <SafeAreaView style={styles.reviewPage}><StatusBar style="light" />
      <View style={styles.reviewTop}><Pressable onPress={onClose} style={styles.closeButton}><Ionicons name="close" size={28} color={c.white} /></Pressable><Text style={styles.reviewTitle}>{item.label}</Text></View>
      <Image source={{ uri: snapshot || item.image_url }} style={styles.fullSnapshot} resizeMode="contain" />
      <ScrollView horizontal contentContainerStyle={styles.snapshotStrip}>{snapshots.map(url => <Pressable key={url} onPress={() => setSnapshot(url)}><Image source={{ uri: url }} style={[styles.snapshotThumb, snapshot === url && styles.snapshotThumbActive]} /></Pressable>)}</ScrollView>
      <ScrollView style={styles.reviewControls} contentContainerStyle={{ paddingBottom: 35 }}>
        <Text style={styles.reviewSection}>Tag this unknown</Text><TextInput value={tag} onChangeText={setTag} placeholder="Unknown tag" placeholderTextColor={c.muted} style={styles.input} /><Button title="Save tag" onPress={saveTag} secondary disabled={busy} />
        <Text style={styles.reviewSection}>Add this person to People</Text><TextInput value={personName} onChangeText={setPersonName} placeholder="Person’s name" placeholderTextColor={c.muted} style={styles.input} /><Button title="Add this person to People" onPress={promote} disabled={busy} />
        <Button title="Delete unknown person" onPress={remove} danger disabled={busy} />
      </ScrollView>
    </SafeAreaView>
  </Modal>;
}

function Unknowns({ items, reload }: { items: Unknown[]; reload: () => Promise<void> }) {
  const [selected, setSelected] = useState<Unknown | null>(null);
  return <><ScrollView contentContainerStyle={styles.content}>
    <Text style={styles.eyebrow}>UNKNOWNS</Text><Text style={styles.hero}>Unknown activity</Text><Text style={styles.sub}>Tap any snapshot to view it full screen, tag the visitor, or add them to People.</Text>
    {items.map(item => <View key={item.id} style={styles.unknownCard}>
      <Pressable onPress={() => setSelected(item)}><Image source={{ uri: item.image_url }} style={styles.unknownImage} /></Pressable>
      <View style={{ flex: 1 }}><Text style={styles.rowTitle}>{item.label}</Text><Text style={styles.muted}>{item.sightings} appearance{item.sightings === 1 ? '' : 's'} · Last seen {when(item.last_seen)}</Text>
        <View style={styles.activityBlock}>{item.activities.slice(0, 4).map(activity => <Pressable key={activity.id} onPress={() => setSelected(item)} style={styles.unknownActivity}>{activity.image_url ? <Image source={{ uri: activity.image_url }} style={styles.activityThumb} /> : <Ionicons name="image-outline" size={28} color={c.muted} />}<View style={{ flex: 1 }}><Text style={styles.activityText}>{when(activity.seen_at)}</Text><Text style={styles.muted}>{activity.device_name || 'Camera device'}</Text></View></Pressable>)}</View>
      </View>
    </View>)}
    {!items.length && <Empty icon="eye-off-outline" text="Nothing to review." />}
  </ScrollView><UnknownReview item={selected} visible={Boolean(selected)} onClose={() => setSelected(null)} reload={reload} /></>;
}

function DetectionChips({ detections, error }: { detections: Detection[]; error?: string }) {
  return <View style={styles.detectionList}>{detections.map((item, index) => <View key={`${item.label}-${index}`} style={styles.chip}><Ionicons name={item.kind === 'known' ? 'person' : 'alert'} color={item.kind === 'known' ? c.mint : c.amber} /><Text style={styles.chipText}>{item.label}{item.kind === 'known' ? ` · ${Math.round(item.confidence * 100)}%` : ''}</Text></View>)}{!!error && <Text style={styles.streamError}>{error}</Text>}</View>;
}

function DevicePills({ devices, selected, onSelect, multiple = false, disabled = false }: { devices: Device[]; selected: number[]; onSelect: (id: number) => void; multiple?: boolean; disabled?: boolean }) {
  return <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.devicePills}>{devices.map(device => {
    const active = selected.includes(device.id);
    return <Pressable key={device.id} disabled={disabled} onPress={() => onSelect(device.id)} style={[styles.devicePill, active && styles.devicePillActive, disabled && { opacity: .65 }]}><View style={[styles.statusDot, { backgroundColor: device.online ? c.mint : c.muted }]} /><Text style={[styles.devicePillText, active && { color: c.white }]}>{device.name}</Text>{multiple && active && <Ionicons name="checkmark" color={c.white} />}</Pressable>;
  })}</ScrollView>;
}

function CameraSource({ devices }: { devices: Device[] }) {
  const video = useRef<HTMLVideoElement>(null); const media = useRef<MediaStream | null>(null); const socket = useRef<WebSocket | null>(null); const canvas = useRef<HTMLCanvasElement | null>(null);
  const recognitionBusy = useRef(false); const lastRecognition = useRef(0); const session = useRef(`${Date.now()}-${Math.random()}`);
  const [deviceId, setDeviceId] = useState<number | null>(devices[0]?.id || null); const [running, setRunning] = useState(false); const [detections, setDetections] = useState<Detection[]>([]); const [error, setError] = useState('');
  useEffect(() => { if (!deviceId && devices.length) setDeviceId(devices[0]!.id); }, [deviceId, devices]);
  const stop = () => { socket.current?.close(); socket.current = null; media.current?.getTracks().forEach(track => track.stop()); media.current = null; if (video.current) video.current.srcObject = null; setRunning(false); };
  const start = async () => {
    if (!deviceId) return setError('Add and select a device first.');
    try {
      setError(''); session.current = `${deviceId}-${Date.now()}-${Math.random()}`;
      const stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: 'user' } });
      media.current = stream; if (video.current) { video.current.srcObject = stream; await video.current.play(); }
      const [serverUrl, auth] = await Promise.all([getApiUrl(), token()]); if (!auth) throw new Error('Sign in again before starting the camera.');
      const ws = new WebSocket(serverUrl.replace(/^http:/, 'ws:').replace(/^https:/, 'wss:') + '/ws/live/source'); socket.current = ws;
      ws.onopen = () => { ws.send(JSON.stringify({ type: 'auth', token: auth, device_id: deviceId })); setRunning(true); };
      ws.onerror = () => setError('Could not open the live stream. Check the backend URL.'); ws.onclose = () => setRunning(false);
    } catch (e) { stop(); setError((e as Error).message); }
  };
  useEffect(() => () => { socket.current?.close(); media.current?.getTracks().forEach(track => track.stop()); }, []);
  useEffect(() => {
    if (!running || !deviceId) return;
    const timer = setInterval(() => {
      const element = video.current; const ws = socket.current;
      if (!element || !element.videoWidth || !ws || ws.readyState !== WebSocket.OPEN) return;
      if (!canvas.current) canvas.current = document.createElement('canvas');
      const surface = canvas.current; surface.width = 640; surface.height = Math.round(640 * element.videoHeight / element.videoWidth);
      const context = surface.getContext('2d'); if (!context) return; context.drawImage(element, 0, 0, surface.width, surface.height);
      if (ws.bufferedAmount < 250_000) ws.send(surface.toDataURL('image/jpeg', STREAM_JPEG_QUALITY));
      const now = Date.now();
      if (!recognitionBusy.current && now - lastRecognition.current >= 850) {
        recognitionBusy.current = true; lastRecognition.current = now;
        surface.toBlob(async blob => {
          if (!blob) { recognitionBusy.current = false; return; }
          try { const form = new FormData(); form.append('session_id', session.current); form.append('device_id', String(deviceId)); form.append('frame', blob, 'frame.jpg'); setDetections(await api<Detection[]>('/recognition/frame', { method: 'POST', body: form })); setError(''); }
          catch (e) { setError((e as Error).message); } finally { recognitionBusy.current = false; }
        }, 'image/jpeg', .72);
      }
    }, STREAM_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [running, deviceId]);
  const selectedDevice = devices.find(device => device.id === deviceId);
  return <View style={{ flex: 1 }}><View style={styles.sourceDeviceBar}><Text style={styles.toolbarLabel}>Broadcast camera as</Text><DevicePills devices={devices} selected={deviceId ? [deviceId] : []} onSelect={setDeviceId} disabled={running} /></View><View style={styles.cameraPage}>
    {React.createElement('video', { ref: video, autoPlay: true, muted: true, playsInline: true, style: { width: '100%', height: '100%', objectFit: 'cover', transform: 'scaleX(-1)', backgroundColor: '#000' } })}
    <View style={styles.cameraShade} pointerEvents="box-none"><View style={styles.cameraTop}><View style={styles.liveDark}><View style={[styles.dot, !running && { backgroundColor: c.muted }]} /><Text style={styles.liveTextDark}>{running ? `${selectedDevice?.name || 'CAMERA'} · 15 FPS` : 'SOURCE PAUSED'}</Text></View></View><DetectionChips detections={detections} error={error} /><Pressable style={[styles.shutter, running && { backgroundColor: c.danger }]} onPress={running ? stop : start}><Ionicons name={running ? 'stop' : 'play'} size={27} color={c.white} /></Pressable></View>
  </View></View>;
}

function FeedTile({ device, feed, large }: { device: Device; feed?: LiveFeed; large: boolean }) {
  const online = feed?.online ?? device.online;
  return <View style={[styles.feedTile, large && styles.feedTileLarge]}>{feed?.frame_data_url ? <Image source={{ uri: feed.frame_data_url }} style={styles.remoteFeed} resizeMode="contain" fadeDuration={0} /> : <View style={styles.feedEmpty}><Ionicons name="videocam-off-outline" size={42} color="#8aa096" /><Text style={styles.feedEmptyTitle}>{online ? 'Waiting for frames' : 'Device offline'}</Text></View>}
    <View style={styles.feedName}><View style={[styles.statusDot, { backgroundColor: online ? '#58df9a' : '#9aa7a1' }]} /><Text style={styles.feedNameText}>{device.name}</Text>{device.recording_enabled && <Text style={styles.feedRec}>● REC</Text>}</View>
    <DetectionChips detections={feed?.detections || []} />
  </View>;
}

function RemoteCameraViewer({ devices }: { devices: Device[] }) {
  const [feeds, setFeeds] = useState<Record<number, LiveFeed>>({}); const [selected, setSelected] = useState<number[]>(devices[0] ? [devices[0].id] : []); const [multiple, setMultiple] = useState(false); const [error, setError] = useState('');
  const devicesRef = useRef(devices);
  useEffect(() => { devicesRef.current = devices; }, [devices]);
  useEffect(() => { setSelected(current => { const valid = current.filter(id => devices.some(device => device.id === id)); return valid.length ? valid : devices[0] ? [devices[0].id] : []; }); }, [devices]);
  useEffect(() => {
    let stopped = false; let connection: WebSocket | null = null; let retry: ReturnType<typeof setTimeout> | null = null;
    const connect = async () => {
      const [serverUrl, auth] = await Promise.all([getApiUrl(), token()]); if (stopped || !auth) return;
      connection = new WebSocket(serverUrl.replace(/^http:/, 'ws:').replace(/^https:/, 'wss:') + '/ws/live/viewer');
      connection.onopen = () => { connection?.send(JSON.stringify({ type: 'auth', token: auth })); setError(''); };
      connection.onmessage = event => {
        if (typeof event.data !== 'string') return;
        try {
          const message = JSON.parse(event.data);
          if (message.type === 'status_batch') setFeeds(current => { const next = { ...current }; for (const device of devicesRef.current) next[device.id] = { ...(next[device.id] || { detections: [] }), online: message.online_device_ids.includes(device.id) }; return next; });
          if (message.type === 'status') setFeeds(current => ({ ...current, [message.device_id]: { ...(current[message.device_id] || { detections: [] }), online: Boolean(message.online) } }));
          if (message.type === 'frame') setFeeds(current => ({ ...current, [message.device_id]: { ...(current[message.device_id] || { detections: [] }), online: true, frame_data_url: message.data_url, captured_at: new Date().toISOString(), device_id: message.device_id } }));
          if (message.type === 'detections') setFeeds(current => ({ ...current, [message.device_id]: { ...(current[message.device_id] || { online: true }), detections: message.detections || [] } }));
        } catch { /* Ignore malformed stream messages. */ }
      };
      connection.onerror = () => setError('Live connection lost. Reconnecting…');
      connection.onclose = () => { setFeeds(current => Object.fromEntries(Object.entries(current).map(([id, feed]) => [id, { ...feed, online: false }]))); if (!stopped) retry = setTimeout(connect, 1500); };
    };
    connect(); return () => { stopped = true; if (retry) clearTimeout(retry); connection?.close(); };
  }, []);
  const select = (id: number) => setSelected(current => multiple ? current.includes(id) ? (current.length > 1 ? current.filter(value => value !== id) : current) : [...current, id] : [id]);
  const visible = devices.filter(device => selected.includes(device.id));
  return <View style={styles.liveViewer}><View style={styles.viewerToolbar}><View style={styles.viewToggle}><Pressable onPress={() => { setMultiple(false); setSelected(current => current.slice(0, 1)); }} style={[styles.toggleButton, !multiple && styles.toggleButtonActive]}><Ionicons name="square-outline" color={!multiple ? c.white : c.text} /><Text style={[styles.toggleText, !multiple && { color: c.white }]}>One feed</Text></Pressable><Pressable onPress={() => setMultiple(true)} style={[styles.toggleButton, multiple && styles.toggleButtonActive]}><Ionicons name="grid-outline" color={multiple ? c.white : c.text} /><Text style={[styles.toggleText, multiple && { color: c.white }]}>Multiple feeds</Text></Pressable></View><DevicePills devices={devices} selected={selected} onSelect={select} multiple={multiple} />{!!error && <Text style={styles.error}>{error}</Text>}</View>
    <ScrollView style={{ flex: 1 }} contentContainerStyle={[styles.feedGrid, !multiple && styles.singleFeedGrid]}>{visible.map(device => <FeedTile key={device.id} device={device} feed={feeds[device.id]} large={!multiple} />)}{!devices.length && <Empty icon="videocam-outline" text="Add a camera device from Overview first." />}</ScrollView>
  </View>;
}

function LiveFeedPage({ devices }: { devices: Device[] }) {
  const [mode, setMode] = useState<'viewer' | 'source'>(Platform.OS === 'web' ? 'source' : 'viewer');
  if (Platform.OS !== 'web') return <RemoteCameraViewer devices={devices} />;
  return <View style={{ flex: 1 }}><View style={styles.modeBar}><Pressable onPress={() => setMode('viewer')} style={[styles.modeButton, mode === 'viewer' && styles.modeButtonActive]}><Ionicons name="eye-outline" color={mode === 'viewer' ? c.white : c.text} /><Text style={[styles.modeText, mode === 'viewer' && { color: c.white }]}>Live viewer</Text></Pressable><Pressable onPress={() => setMode('source')} style={[styles.modeButton, mode === 'source' && styles.modeButtonActive]}><Ionicons name="laptop-outline" color={mode === 'source' ? c.white : c.text} /><Text style={[styles.modeText, mode === 'source' && { color: c.white }]}>Camera source</Text></Pressable></View>{mode === 'source' ? <CameraSource devices={devices} /> : <RemoteCameraViewer devices={devices} />}</View>;
}

function RecordingsPage({ devices, recordings, reload }: { devices: Device[]; recordings: Recording[]; reload: () => Promise<void> }) {
  const [busy, setBusy] = useState<number | null>(null);
  const toggle = async (device: Device, enabled: boolean) => {
    setBusy(device.id);
    try { const form = new FormData(); form.append('enabled', String(enabled)); await api(`/devices/${device.id}/recording`, { method: 'PATCH', body: form }); await reload(); }
    catch (e) { Alert.alert('Could not change recording', (e as Error).message); } finally { setBusy(null); }
  };
  return <ScrollView contentContainerStyle={styles.content}><Text style={styles.eyebrow}>RECORDINGS</Text><Text style={styles.hero}>CCTV backup</Text><Text style={styles.sub}>Enabled devices record whenever their camera source is online, even when Live Feed is not being watched.</Text>
    <Text style={styles.sectionTitle}>Recording devices</Text>{devices.map(device => <View key={device.id} style={styles.recordingDevice}><View style={[styles.deviceIcon, device.online && styles.deviceIconOnline]}><Ionicons name="videocam-outline" size={21} color={device.online ? c.white : c.muted} /></View><View style={{ flex: 1 }}><Text style={styles.rowTitle}>{device.name}</Text><Text style={styles.muted}>{device.online ? 'Source online' : 'Waiting for source'} · {device.recording_enabled ? 'Recording enabled' : 'Recording off'}</Text></View>{busy === device.id ? <ActivityIndicator color={c.mint} /> : <Switch value={device.recording_enabled} onValueChange={value => toggle(device, value)} trackColor={{ false: '#cad8d0', true: '#8ed9b4' }} thumbColor={device.recording_enabled ? c.mint : '#f5f5f5'} />}</View>)}
    <AddDevice reload={reload} compact /><Text style={styles.sectionTitle}>Saved footage</Text>{recordings.map(recording => <Pressable key={recording.id} style={styles.recordingRow} onPress={() => Linking.openURL(recording.video_url)}><View style={styles.playIcon}><Ionicons name="play" size={20} color={c.white} /></View><View style={{ flex: 1 }}><Text style={styles.rowTitle}>{recording.device_name}</Text><Text style={styles.muted}>{when(recording.started_at)} · {Math.max(1, Math.round((new Date(recording.ended_at).getTime() - new Date(recording.started_at).getTime()) / 60000))} min</Text></View><Ionicons name="open-outline" size={20} color={c.mint} /></Pressable>)}{!recordings.length && <Empty icon="film-outline" text="Completed recording segments will appear here." />}</ScrollView>;
}

function SettingsPage() {
  return <ScrollView contentContainerStyle={styles.content}><Text style={styles.eyebrow}>SETTINGS</Text><Text style={styles.hero}>Settings</Text><Text style={styles.sub}>This section is reserved for future account, notification, storage, and retention settings.</Text><View style={styles.dormantCard}><Ionicons name="settings-outline" size={42} color={c.mint} /><Text style={styles.cardTitle}>Settings are dormant</Text><Text style={[styles.muted, { textAlign: 'center' }]}>No controls are active in this tab yet.</Text></View></ScrollView>;
}

function AppShell({ user, logout }: { user: User; logout: () => void }) {
  const [tab, setTab] = useState<Tab>('home'); const [people, setPeople] = useState<Person[]>([]); const [unknowns, setUnknowns] = useState<Unknown[]>([]); const [devices, setDevices] = useState<Device[]>([]); const [recordings, setRecordings] = useState<Recording[]>([]); const { width } = useWindowDimensions();
  const reload = useCallback(async () => { try { const [nextPeople, nextUnknowns, nextDevices, nextRecordings] = await Promise.all([api<Person[]>('/people'), api<Unknown[]>('/unknowns'), api<Device[]>('/devices'), api<Recording[]>('/recordings')]); setPeople(nextPeople); setUnknowns(nextUnknowns); setDevices(nextDevices); setRecordings(nextRecordings); } catch { /* Connection errors surface in feature views. */ } }, []);
  useEffect(() => { reload(); }, [reload, tab]);
  useEffect(() => { const timer = setInterval(() => api<Device[]>('/devices').then(setDevices).catch(() => {}), 4000); return () => clearInterval(timer); }, []);
  const body = tab === 'home' ? <Home user={user} people={people} unknowns={unknowns} devices={devices} go={setTab} reload={reload} /> : tab === 'live' ? <LiveFeedPage devices={devices} /> : tab === 'people' ? <People items={people} reload={reload} /> : tab === 'unknowns' ? <Unknowns items={unknowns} reload={reload} /> : tab === 'recordings' ? <RecordingsPage devices={devices} recordings={recordings} reload={reload} /> : <SettingsPage />;
  const tabs: { id: Tab; icon: keyof typeof Ionicons.glyphMap; label: string }[] = [
    { id: 'home', icon: 'grid-outline', label: 'Overview' }, { id: 'live', icon: 'videocam-outline', label: 'Live Feed' },
    { id: 'people', icon: 'people-outline', label: 'People' }, { id: 'unknowns', icon: 'alert-circle-outline', label: 'Unknowns' },
    { id: 'recordings', icon: 'film-outline', label: 'Recordings' }, { id: 'settings', icon: 'settings-outline', label: 'Settings' }
  ];
  return <SafeAreaView style={styles.app}><StatusBar style="dark" />{width > 850 && <View style={styles.side}><Text style={styles.sideBrand}>◉ FACE RECOGNITION</Text>{tabs.map(item => <Nav key={item.id} {...item} active={tab === item.id} onPress={() => setTab(item.id)} />)}<View style={{ flex: 1 }} /><Pressable onPress={logout}><Text style={styles.logout}>Sign out</Text></Pressable></View>}<View style={{ flex: 1 }}>{body}{width <= 850 && <View style={styles.bottom}>{tabs.map(item => <Nav key={item.id} {...item} active={tab === item.id} onPress={() => setTab(item.id)} compact />)}</View>}</View></SafeAreaView>;
}

function Nav({ icon, label, active, onPress, compact = false }: { icon: keyof typeof Ionicons.glyphMap; label: string; active: boolean; onPress: () => void; compact?: boolean }) {
  return <Pressable onPress={onPress} style={[styles.nav, compact && styles.navCompact, active && styles.navActive]}><Ionicons name={icon} size={compact ? 20 : 21} color={active ? c.mint : c.muted} /><Text numberOfLines={1} style={[styles.navText, compact && styles.navTextCompact, active && { color: c.text }]}>{label}</Text></Pressable>;
}

export default function App() {
  const [user, setUser] = useState<User | null>(null); const [loading, setLoading] = useState(true);
  useEffect(() => { token().then(value => value ? api<User>('/auth/me').then(setUser).catch(() => setToken(null)) : null).finally(() => setLoading(false)); }, []);
  if (loading) return <View style={styles.center}><ActivityIndicator color={c.mint} /></View>;
  return user ? <AppShell user={user} logout={async () => { await setToken(null); setUser(null); }} /> : <Auth onDone={setUser} />;
}

const styles = StyleSheet.create({
  app: { flex: 1, backgroundColor: c.bg, flexDirection: 'row' }, center: { flex: 1, backgroundColor: c.bg, alignItems: 'center', justifyContent: 'center', gap: 18, padding: 28 },
  authPage: { flex: 1, backgroundColor: c.bg, alignItems: 'center', justifyContent: 'center', padding: 20 }, authCard: { width: '100%', maxWidth: 460, backgroundColor: c.panel, padding: 32, borderRadius: 24, borderWidth: 1, borderColor: c.line, shadowColor: '#1f513c', shadowOpacity: .08, shadowRadius: 18, elevation: 3 },
  logo: { width: 56, height: 56, backgroundColor: c.mint, borderRadius: 18, alignItems: 'center', justifyContent: 'center', marginBottom: 15 }, brand: { color: c.mint, fontWeight: '900', letterSpacing: 3, marginBottom: 28 }, authTitle: { color: c.text, fontSize: 29, fontWeight: '700' }, hero: { color: c.text, fontSize: 34, lineHeight: 41, fontWeight: '700', marginBottom: 5 }, sub: { color: c.muted, fontSize: 15, lineHeight: 22, marginBottom: 25 },
  input: { backgroundColor: c.panel, color: c.text, borderWidth: 1, borderColor: c.line, borderRadius: 12, paddingHorizontal: 15, paddingVertical: 14, marginBottom: 12, fontSize: 15 }, button: { backgroundColor: c.mint, borderRadius: 12, padding: 15, alignItems: 'center', marginTop: 4, marginBottom: 10 }, buttonSecondary: { backgroundColor: c.panel2, borderWidth: 1, borderColor: c.line }, buttonDanger: { backgroundColor: c.danger }, buttonText: { color: c.white, fontWeight: '800', fontSize: 15 }, link: { color: c.cyan, textAlign: 'center', padding: 10 }, linkInline: { color: c.mint, fontWeight: '700', fontSize: 13 }, privacy: { color: c.muted, fontSize: 11, textAlign: 'center', marginTop: 16 }, error: { color: c.danger, marginBottom: 10 },
  side: { width: 230, padding: 24, borderRightWidth: 1, borderRightColor: c.line, backgroundColor: c.panel }, sideBrand: { color: c.mint, fontWeight: '900', letterSpacing: 2, marginBottom: 35 }, nav: { flexDirection: 'row', alignItems: 'center', gap: 10, padding: 13, borderRadius: 10, marginBottom: 4 }, navCompact: { flex: 1, flexDirection: 'column', gap: 2, paddingHorizontal: 1, paddingVertical: 8, marginBottom: 0 }, navActive: { backgroundColor: c.panel2 }, navText: { color: c.muted, fontSize: 13, fontWeight: '600' }, navTextCompact: { fontSize: 9 }, logout: { color: c.danger, padding: 12 }, bottom: { minHeight: 68, flexDirection: 'row', backgroundColor: c.panel, borderTopWidth: 1, borderTopColor: c.line, justifyContent: 'space-around' },
  content: { padding: 24, paddingBottom: 110, width: '100%', maxWidth: 1050, alignSelf: 'center' }, eyebrow: { color: c.mint, fontSize: 11, fontWeight: '800', letterSpacing: 2, marginBottom: 10 }, monitorCard: { minHeight: 170, borderRadius: 20, padding: 24, backgroundColor: '#e8f7ef', borderWidth: 1, borderColor: '#bfe7d1', flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 18 }, live: { alignSelf: 'flex-start', flexDirection: 'row', alignItems: 'center', gap: 7, backgroundColor: '#d5f1e2', borderRadius: 20, paddingHorizontal: 10, paddingVertical: 6, marginBottom: 15 }, dot: { width: 7, height: 7, borderRadius: 4, backgroundColor: c.mint }, liveText: { color: '#147247', fontSize: 10, fontWeight: '900', letterSpacing: 1 }, cardTitle: { color: c.text, fontSize: 21, fontWeight: '700', marginBottom: 8 }, muted: { color: c.muted, fontSize: 13, lineHeight: 19 }, roundIcon: { width: 48, height: 48, borderRadius: 24, backgroundColor: c.mint, alignItems: 'center', justifyContent: 'center', marginLeft: 15 },
  stats: { flexDirection: 'row', gap: 10, marginBottom: 28 }, stat: { flex: 1, backgroundColor: c.panel, borderWidth: 1, borderColor: c.line, borderRadius: 15, padding: 16 }, statNum: { color: c.text, fontSize: 25, fontWeight: '800' }, statLabel: { color: c.muted, fontSize: 11, marginTop: 4 }, sectionHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }, sectionTitle: { color: c.text, fontSize: 18, fontWeight: '700', marginTop: 14, marginBottom: 12 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 13, backgroundColor: c.panel, borderWidth: 1, borderColor: c.line, borderRadius: 14, padding: 13, marginBottom: 9 }, rowTitle: { color: c.text, fontWeight: '700', marginBottom: 3 }, avatar: { width: 50, height: 50, borderRadius: 11, backgroundColor: c.panel2 }, empty: { alignItems: 'center', gap: 10, padding: 35, borderWidth: 1, borderStyle: 'dashed', borderColor: c.line, borderRadius: 16, backgroundColor: c.panel }, formCard: { backgroundColor: c.panel, borderRadius: 18, borderWidth: 1, borderColor: c.line, padding: 18, marginBottom: 16 },
  addDevice: { flexDirection: 'row', alignItems: 'center', marginBottom: 18 }, pairButton: { minHeight: 49, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, backgroundColor: c.mint, borderRadius: 12, paddingHorizontal: 18, paddingVertical: 12 }, smallGreenText: { color: c.white, fontWeight: '800', fontSize: 12 }, deviceRow: { flexDirection: 'row', alignItems: 'center', gap: 12, backgroundColor: c.panel, borderWidth: 1, borderColor: c.line, borderRadius: 14, padding: 13, marginBottom: 8 }, deviceIcon: { width: 42, height: 42, borderRadius: 12, backgroundColor: c.panel2, alignItems: 'center', justifyContent: 'center' }, deviceIconOnline: { backgroundColor: c.mint }, recordingBadge: { flexDirection: 'row', gap: 5, alignItems: 'center', backgroundColor: '#fff0f0', paddingHorizontal: 8, paddingVertical: 5, borderRadius: 12 }, recordingDot: { width: 7, height: 7, borderRadius: 4, backgroundColor: c.danger }, recordingBadgeText: { color: c.danger, fontSize: 10, fontWeight: '900' }, removeDeviceButton: { width: 38, height: 38, borderRadius: 10, alignItems: 'center', justifyContent: 'center', backgroundColor: '#fff0f0', borderWidth: 1, borderColor: '#f2caca' },
  pairPage: { flex: 1, backgroundColor: c.bg }, pairTop: { minHeight: 64, flexDirection: 'row', alignItems: 'center', paddingHorizontal: 14, backgroundColor: c.panel, borderBottomWidth: 1, borderBottomColor: c.line }, pairTitle: { color: c.text, fontSize: 18, fontWeight: '800', marginLeft: 8 }, pairBody: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 22, width: '100%', maxWidth: 620, alignSelf: 'center' }, qrCard: { alignItems: 'center', gap: 14, padding: 22, backgroundColor: c.panel, borderWidth: 1, borderColor: c.line, borderRadius: 20 }, qrFrame: { padding: 15, backgroundColor: c.white, borderRadius: 12 }, pairSuccess: { alignItems: 'center', gap: 12, minWidth: 270 }, permissionCard: { alignItems: 'center', gap: 13, backgroundColor: c.panel, borderWidth: 1, borderColor: c.line, borderRadius: 20, padding: 28, maxWidth: 400 }, scannerFrame: { width: '100%', maxWidth: 480, height: '72%', minHeight: 390, borderRadius: 22, overflow: 'hidden', backgroundColor: '#000' }, scanner: { flex: 1 }, scanGuide: { position: 'absolute', width: 230, height: 230, borderWidth: 3, borderColor: '#73e4a9', borderRadius: 20, alignSelf: 'center', top: '24%' }, scanHint: { position: 'absolute', left: 20, right: 20, bottom: 22, color: c.white, fontWeight: '700', textAlign: 'center', backgroundColor: 'rgba(0,0,0,.62)', borderRadius: 18, padding: 11 }, nameDeviceCard: { width: '100%', maxWidth: 440, alignItems: 'center', gap: 12, backgroundColor: c.panel, borderWidth: 1, borderColor: c.line, borderRadius: 20, padding: 24 },
  personCard: { backgroundColor: c.panel, borderWidth: 1, borderColor: c.line, borderRadius: 16, padding: 15, marginBottom: 10 }, personTop: { flexDirection: 'row', alignItems: 'center', gap: 12 }, initial: { width: 50, height: 50, borderRadius: 14, backgroundColor: '#dcf4e7', alignItems: 'center', justifyContent: 'center' }, initialText: { color: c.mint, fontSize: 20, fontWeight: '800' }, personActions: { flexDirection: 'row', gap: 6 }, iconButtonGreen: { width: 40, height: 40, borderRadius: 10, backgroundColor: c.mint, alignItems: 'center', justifyContent: 'center' }, iconButtonDanger: { width: 40, height: 40, borderRadius: 10, backgroundColor: '#fff0f0', borderWidth: 1, borderColor: '#f2caca', alignItems: 'center', justifyContent: 'center' }, activityBlock: { marginTop: 12, paddingTop: 10, borderTopWidth: 1, borderTopColor: c.line }, activityHeading: { color: c.text, fontWeight: '700', fontSize: 12, marginBottom: 5 }, activityLine: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingVertical: 3 }, activityText: { color: c.muted, fontSize: 12, flex: 1 },
  unknownCard: { flexDirection: 'row', gap: 14, backgroundColor: c.panel, borderWidth: 1, borderColor: c.line, borderRadius: 16, padding: 13, marginBottom: 10 }, unknownImage: { width: 92, height: 112, borderRadius: 12, backgroundColor: c.panel2 }, unknownActivity: { flexDirection: 'row', alignItems: 'center', gap: 9, marginBottom: 7 }, activityThumb: { width: 38, height: 38, borderRadius: 8, backgroundColor: c.panel2 },
  reviewPage: { flex: 1, backgroundColor: '#101713' }, reviewTop: { minHeight: 58, flexDirection: 'row', alignItems: 'center', paddingHorizontal: 14 }, closeButton: { width: 42, height: 42, alignItems: 'center', justifyContent: 'center' }, reviewTitle: { color: c.white, fontSize: 18, fontWeight: '700', marginLeft: 8 }, fullSnapshot: { width: '100%', height: '42%', backgroundColor: '#000' }, snapshotStrip: { gap: 8, padding: 10 }, snapshotThumb: { width: 64, height: 64, borderRadius: 9, borderWidth: 2, borderColor: 'transparent' }, snapshotThumbActive: { borderColor: '#58df9a' }, reviewControls: { flex: 1, backgroundColor: c.bg, borderTopLeftRadius: 20, borderTopRightRadius: 20, padding: 18 }, reviewSection: { color: c.text, fontWeight: '800', marginTop: 10, marginBottom: 8 },
  modeBar: { flexDirection: 'row', gap: 8, padding: 10, justifyContent: 'center', backgroundColor: c.panel, borderBottomWidth: 1, borderBottomColor: c.line }, modeButton: { flexDirection: 'row', alignItems: 'center', gap: 7, borderRadius: 20, paddingHorizontal: 15, paddingVertical: 9, backgroundColor: c.panel2 }, modeButtonActive: { backgroundColor: c.mint }, modeText: { color: c.text, fontWeight: '700', fontSize: 12 }, sourceDeviceBar: { backgroundColor: c.panel, borderBottomWidth: 1, borderBottomColor: c.line, paddingHorizontal: 12, paddingTop: 8 }, toolbarLabel: { color: c.muted, fontSize: 11, fontWeight: '700', marginLeft: 4 }, devicePills: { gap: 7, paddingVertical: 9 }, devicePill: { flexDirection: 'row', alignItems: 'center', gap: 6, backgroundColor: c.panel2, borderWidth: 1, borderColor: c.line, borderRadius: 18, paddingHorizontal: 11, paddingVertical: 7 }, devicePillActive: { backgroundColor: c.mint, borderColor: c.mint }, devicePillText: { color: c.text, fontSize: 12, fontWeight: '700' }, statusDot: { width: 7, height: 7, borderRadius: 4 },
  cameraPage: { flex: 1, backgroundColor: '#000' }, cameraShade: { ...StyleSheet.absoluteFill, alignItems: 'center', justifyContent: 'center', padding: 24 }, cameraTop: { position: 'absolute', top: 22, left: 20 }, liveDark: { flexDirection: 'row', alignItems: 'center', gap: 7, backgroundColor: 'rgba(8,26,18,.86)', borderRadius: 20, paddingHorizontal: 11, paddingVertical: 7 }, liveTextDark: { color: '#8cf0ba', fontSize: 10, fontWeight: '900', letterSpacing: 1 }, detectionList: { position: 'absolute', left: 16, right: 16, bottom: 94, alignItems: 'center' }, chip: { flexDirection: 'row', gap: 8, backgroundColor: 'rgba(255,255,255,.94)', borderWidth: 1, borderColor: '#cee1d6', borderRadius: 22, paddingHorizontal: 14, paddingVertical: 9, marginTop: 6 }, chipText: { color: c.text, fontWeight: '700' }, streamError: { color: '#ff9898', backgroundColor: 'rgba(20,0,0,.7)', padding: 8, borderRadius: 8 }, shutter: { position: 'absolute', bottom: 24, width: 62, height: 62, borderRadius: 31, backgroundColor: c.mint, alignItems: 'center', justifyContent: 'center' },
  liveViewer: { flex: 1, backgroundColor: c.bg }, viewerToolbar: { backgroundColor: c.panel, borderBottomWidth: 1, borderBottomColor: c.line, paddingHorizontal: 12, paddingTop: 10 }, viewToggle: { flexDirection: 'row', gap: 7 }, toggleButton: { flexDirection: 'row', alignItems: 'center', gap: 6, backgroundColor: c.panel2, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 8 }, toggleButtonActive: { backgroundColor: c.mint }, toggleText: { color: c.text, fontSize: 12, fontWeight: '700' }, feedGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 10, padding: 10, minHeight: '100%' }, singleFeedGrid: { flexDirection: 'column', flexWrap: 'nowrap' }, feedTile: { width: '48.5%', minWidth: 290, height: 290, borderRadius: 15, overflow: 'hidden', backgroundColor: '#07110c', borderWidth: 1, borderColor: '#bcd6c7' }, feedTileLarge: { width: '100%', minHeight: 500, flex: 1 }, remoteFeed: { flex: 1, width: '100%', backgroundColor: '#000' }, feedEmpty: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 9 }, feedEmptyTitle: { color: '#b7c7bf', fontWeight: '700' }, feedName: { position: 'absolute', left: 12, top: 12, flexDirection: 'row', alignItems: 'center', gap: 7, backgroundColor: 'rgba(7,22,15,.84)', borderRadius: 16, paddingHorizontal: 10, paddingVertical: 7 }, feedNameText: { color: c.white, fontWeight: '800', fontSize: 12 }, feedRec: { color: '#ff7777', fontSize: 10, fontWeight: '900' },
  recordingDevice: { flexDirection: 'row', alignItems: 'center', gap: 12, backgroundColor: c.panel, borderWidth: 1, borderColor: c.line, borderRadius: 14, padding: 14, marginBottom: 8 }, recordingRow: { flexDirection: 'row', alignItems: 'center', gap: 12, backgroundColor: c.panel, borderWidth: 1, borderColor: c.line, borderRadius: 14, padding: 13, marginBottom: 8 }, playIcon: { width: 42, height: 42, borderRadius: 21, backgroundColor: c.mint, alignItems: 'center', justifyContent: 'center' }, dormantCard: { minHeight: 260, backgroundColor: c.panel, borderWidth: 1, borderColor: c.line, borderRadius: 18, alignItems: 'center', justifyContent: 'center', gap: 12, padding: 30 }
});
