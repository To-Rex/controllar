import SwiftUI

public struct SettingsView: View {
    @ObservedObject var model: AppModel

    @State private var name: String = ""
    @State private var port: String = ""
    @State private var showHotkeyRecorder = false
    @State private var autostartEnabled = Autostart.isEnabled
    @State private var autostartError: String?
    @State private var revealFingerprints = false

    public init(model: AppModel) {
        self.model = model
    }

    public var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text("Settings")
                    .font(.system(size: 22, weight: .semibold, design: .rounded))

                generalSection
                behaviorSection
                clipboardSection
                permissionsSection
                securitySection
                aboutSection
            }
            .padding(24)
            .frame(maxWidth: 860, alignment: .leading)
            .frame(maxWidth: .infinity)
        }
        .onAppear {
            name = model.status?.local.name ?? ""
            port = String(model.status?.local.port ?? 24800)
            autostartEnabled = Autostart.isEnabled
        }
        .sheet(isPresented: $showHotkeyRecorder) {
            HotkeyRecorderSheet(current: model.status?.hotkey) { modifiers, usage in
                model.setHotkey(modifiers: modifiers, usage: usage)
            }
        }
    }

    // MARK: Sections

    private var generalSection: some View {
        VStack(alignment: .leading, spacing: 14) {
            SectionHeader("General")
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Computer name").font(.system(.callout, weight: .medium))
                    Text("Shown to the other computers").font(.system(.caption)).foregroundStyle(DS.textDim)
                }
                Spacer()
                TextField("Name", text: $name)
                    .textFieldStyle(.plain)
                    .frame(width: 220)
                    .padding(8)
                    .background(DS.cardAlt, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous).stroke(DS.border, lineWidth: 1))
                Button("Save") { model.rename(name) }
                    .buttonStyle(SecondaryButtonStyle())
                    .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty)
            }

            Divider()

            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Mode").font(.system(.callout, weight: .medium))
                    Text(model.mode.explanation).font(.system(.caption)).foregroundStyle(DS.textDim)
                }
                Spacer()
                Picker("", selection: Binding(
                    get: { model.mode },
                    set: { model.setMode($0) }
                )) {
                    Text("Server").tag(FLMode.server)
                    Text("Client").tag(FLMode.client)
                }
                .pickerStyle(.segmented)
                .frame(width: 200)
            }

            Divider()

            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Launch at login").font(.system(.callout, weight: .medium))
                    Text("Keeps FlowLink ready in the menu bar after a restart")
                        .font(.system(.caption))
                        .foregroundStyle(DS.textDim)
                }
                Spacer()
                if let autostartError {
                    Text(autostartError)
                        .font(.system(.caption))
                        .foregroundStyle(DS.err)
                }
                Toggle("", isOn: Binding(
                    get: { autostartEnabled },
                    set: { newValue in
                        do {
                            try Autostart.set(newValue)
                            autostartEnabled = newValue
                            autostartError = nil
                        } catch {
                            autostartEnabled = Autostart.isEnabled
                            autostartError = "Couldn't change: run the app from an .app bundle."
                        }
                    }
                ))
                .toggleStyle(.switch)
                .labelsHidden()
            }

            Divider()

            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Engine").font(.system(.callout, weight: .medium))
                    Text(model.isRunning ? "Running — \(model.status?.stateLabel ?? "")" : "Stopped")
                        .font(.system(.caption))
                        .foregroundStyle(DS.textDim)
                }
                Spacer()
                if model.isRunning {
                    Button("Stop") { model.stopEngine() }
                        .buttonStyle(SecondaryButtonStyle())
                } else {
                    Button("Start") { model.startEngine() }
                        .buttonStyle(PrimaryButtonStyle())
                }
            }
        }
        .card()
    }

    private var behaviorSection: some View {
        VStack(alignment: .leading, spacing: 14) {
            SectionHeader("Connection")
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Listen port").font(.system(.callout, weight: .medium))
                    Text("Default 24800 — must match on the other computer when connecting manually")
                        .font(.system(.caption))
                        .foregroundStyle(DS.textDim)
                }
                Spacer()
                TextField("24800", text: $port)
                    .textFieldStyle(.plain)
                    .font(.system(.callout, design: .monospaced))
                    .frame(width: 80)
                    .padding(8)
                    .background(DS.cardAlt, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous).stroke(DS.border, lineWidth: 1))
                Button("Apply") {
                    if let value = UInt16(port), value > 0 {
                        model.setListenPort(value)
                    } else {
                        port = String(model.status?.local.port ?? 24800)
                    }
                }
                .buttonStyle(SecondaryButtonStyle())
            }

            Divider()

            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Cursor edge").font(.system(.callout, weight: .medium))
                    Text("The edge of this screen that faces the other computer — crossing it moves the cursor there")
                        .font(.system(.caption))
                        .foregroundStyle(DS.textDim)
                }
                Spacer()
                Picker("", selection: Binding(
                    get: { model.status?.edge ?? .right },
                    set: { model.setEdge($0) }
                )) {
                    ForEach(FLEdge.allCases, id: \.self) { edge in
                        Text(edge.title).tag(edge)
                    }
                }
                .pickerStyle(.segmented)
                .frame(width: 240)
            }

            Divider()

            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Toggle hotkey").font(.system(.callout, weight: .medium))
                    Text("Switches control between computers from the server keyboard")
                        .font(.system(.caption))
                        .foregroundStyle(DS.textDim)
                }
                Spacer()
                if let hotkey = model.status?.hotkey {
                    KeyCap(KeyCodes.hotkeyDisplay(modifiers: hotkey.modifiers, usage: hotkey.usage))
                }
                Button("Change") { showHotkeyRecorder = true }
                    .buttonStyle(SecondaryButtonStyle())
            }
        }
        .card()
    }

    private var clipboardSection: some View {
        VStack(alignment: .leading, spacing: 14) {
            SectionHeader("Clipboard")
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Sync clipboard text").font(.system(.callout, weight: .medium))
                    Text("Text up to 1 MB, both directions. Passwords and transient clipboard content are never relayed.")
                        .font(.system(.caption))
                        .foregroundStyle(DS.textDim)
                }
                Spacer()
                Toggle("", isOn: Binding(
                    get: { model.status?.clipboardEnabled ?? true },
                    set: { model.setClipboardEnabled($0) }
                ))
                .toggleStyle(.switch)
                .labelsHidden()
            }
        }
        .card()
    }

    private var permissionsSection: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                SectionHeader("Permissions")
                Spacer()
                if model.permissions.allGranted {
                    Text("All granted")
                        .font(.system(.caption, weight: .semibold))
                        .foregroundStyle(DS.ok)
                } else {
                    Button("Open System Settings") { model.permissions.openPrivacySettings() }
                        .buttonStyle(QuietButtonStyle())
                }
            }
            PermissionRow(
                permissions: model.permissions,
                symbol: "accessibility",
                title: "Accessibility",
                detail: "Required to capture and inject keyboard & mouse events.",
                granted: model.permissions.accessibility,
                action: { model.permissions.requestAccessibility() }
            )
            PermissionRow(
                permissions: model.permissions,
                symbol: "mic.badge.plus",
                title: "Input Monitoring",
                detail: "Required to observe raw input events.",
                granted: model.permissions.inputMonitoring,
                action: { model.permissions.requestInputMonitoring() }
            )
        }
        .card()
    }

    private var securitySection: some View {
        VStack(alignment: .leading, spacing: 14) {
            SectionHeader("Security")
            VStack(alignment: .leading, spacing: 6) {
                Text("This computer's fingerprint").font(.system(.callout, weight: .medium))
                if let local = model.status?.local {
                    if revealFingerprints {
                        FingerprintText(fingerprint: local.fingerprint, compact: false)
                    } else {
                        HStack(spacing: 8) {
                            FingerprintText(fingerprint: local.fingerprint)
                            Button("Show") { revealFingerprints = true }
                                .buttonStyle(QuietButtonStyle())
                        }
                    }
                }
            }

            if let paired = model.status?.pairedDevices, !paired.isEmpty {
                Divider()
                Text("Paired computers").font(.system(.callout, weight: .medium))
                VStack(spacing: 8) {
                    ForEach(paired) { device in
                        HStack(spacing: 10) {
                            Image(systemName: "checkmark.shield")
                                .foregroundStyle(DS.ok)
                            VStack(alignment: .leading, spacing: 1) {
                                Text(device.name).font(.system(.callout, weight: .medium))
                                Text(device.lastAddr ?? "—")
                                    .font(.system(.caption2, design: .monospaced))
                                    .foregroundStyle(DS.textDim)
                            }
                            Spacer()
                            FingerprintText(fingerprint: device.fingerprint)
                            Button("Forget") { model.forget(deviceID: device.id) }
                                .buttonStyle(QuietButtonStyle())
                        }
                        .padding(8)
                        .background(DS.cardAlt, in: RoundedRectangle(cornerRadius: DS.radiusSmall, style: .continuous))
                    }
                }
            }
        }
        .card()
    }

    private var aboutSection: some View {
        VStack(alignment: .leading, spacing: 14) {
            SectionHeader("About")
            StatRow(label: "FlowLink", value: "v\(model.status?.version ?? CoreVersion.string)", mono: true)
            HStack(spacing: 10) {
                Button("Open logs folder") {
                    if let dir = model.logsDirectory() {
                        NSWorkspace.shared.open(URL(fileURLWithPath: dir))
                    }
                }
                .buttonStyle(SecondaryButtonStyle())
                Button("Open config folder") {
                    NSWorkspace.shared.open(URL(fileURLWithPath: AppModel.configDirectory()))
                }
                .buttonStyle(SecondaryButtonStyle())
            }
            Text("Keyboard, mouse and clipboard sharing between macOS and Windows. Traffic is encrypted with TLS 1.3; device identities are pinned after pairing.")
                .font(.system(.caption))
                .foregroundStyle(DS.textFaint)
                .fixedSize(horizontal: false, vertical: true)
        }
        .card()
    }
}
