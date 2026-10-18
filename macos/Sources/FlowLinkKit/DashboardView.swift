import SwiftUI

/// Main dashboard — adapts to the active mode (server / client).
public struct DashboardView: View {
    @ObservedObject var model: AppModel

    @State private var showPairing = false
    @State private var showScanner = false
    @State private var showHotkeyRecorder = false
    @State private var manualHost = ""
    @State private var manualPort = "24800"
    @State private var manualCode = ""
    @State private var pasteLink = ""

    public init(model: AppModel) {
        self.model = model
    }

    public var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                header
                if model.isServer {
                    serverContent
                } else {
                    clientContent
                }
                if let error = model.status?.error, !error.isEmpty {
                    errorCard(error)
                }
                NoticeTicker(notices: model.notices)
            }
            .padding(24)
            .frame(maxWidth: 860, alignment: .leading)
            .frame(maxWidth: .infinity)
        }
        .sheet(isPresented: $showPairing) {
            PairingSheet(model: model)
        }
        .sheet(isPresented: $showScanner) {
            QRScannerSheet { code in
                model.connectLink(code)
            }
        }
        .sheet(isPresented: $showHotkeyRecorder) {
            HotkeyRecorderSheet(current: model.status?.hotkey) { modifiers, usage in
                model.setHotkey(modifiers: modifiers, usage: usage)
            }
        }
    }

    // MARK: Header

    private var header: some View {
        HStack(alignment: .center) {
            VStack(alignment: .leading, spacing: 4) {
                Text(model.statusHeadline)
                    .font(.system(size: 22, weight: .semibold, design: .rounded))
                HStack(spacing: 8) {
                    Text(model.mode == .server ? "Server mode" : "Client mode")
                        .font(.system(.caption, weight: .semibold))
                        .foregroundStyle(DS.accent)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 3)
                        .background(DS.accentSoft, in: Capsule())
                    if let local = model.status?.local {
                        FingerprintText(fingerprint: local.fingerprint)
                    }
                }
            }
            Spacer()
            StatusPill(status: model.status)
        }
    }

    // MARK: Server

    private var serverContent: some View {
        VStack(alignment: .leading, spacing: 16) {
            if let peer = model.status?.peer {
                peerCard(peer)
            } else {
                pairingCard
            }

            HStack(alignment: .top, spacing: 16) {
                edgeCard
                metricsCard
            }

            securityFooter
        }
    }

    private var pairingCard: some View {
        VStack(alignment: .leading, spacing: 14) {
            SectionHeader("Pair a computer")
            HStack(alignment: .center, spacing: 22) {
                VStack(alignment: .leading, spacing: 6) {
                    if let code = model.status?.pairing?.formattedCode {
                        Text(code)
                            .font(DS.monoLarge)
                            .foregroundStyle(DS.text)
                            .textSelection(.enabled)
                        if let remaining = model.status?.pairing?.expiresInMs, remaining > 0 {
                            Text("expires in \(Int(remaining / 1000)) seconds")
                                .font(.system(.caption))
                                .foregroundStyle(DS.textDim)
                        } else {
                            Text("expired — generate a new code")
                                .font(.system(.caption))
                                .foregroundStyle(DS.warn)
                        }
                    } else {
                        Text("— — — —  — —")
                            .font(DS.monoLarge)
                            .foregroundStyle(DS.textFaint)
                    }
                    Text("On the other computer, open FlowLink → Client mode → enter this code or scan the QR.")
                        .font(.system(.caption))
                        .foregroundStyle(DS.textDim)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: 320, alignment: .leading)
                }
                Spacer()
                VStack(spacing: 8) {
                    Button {
                        showPairing = true
                    } label: {
                        Label("Show QR code", systemImage: "qrcode")
                    }
                    .buttonStyle(PrimaryButtonStyle())
                    Button("New code") { model.regenerateCode() }
                        .buttonStyle(SecondaryButtonStyle())
                }
            }
        }
        .card()
    }

    private func peerCard(_ peer: FLPeerInfo) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                SectionHeader("Connected computer")
                Spacer()
                HStack(spacing: 5) {
                    Circle().fill(DS.ok).frame(width: 7, height: 7)
                    Text(model.status?.remoteActive == true ? "Driving" : "Idle")
                        .font(.system(.caption, weight: .semibold))
                        .foregroundStyle(DS.ok)
                }
            }
            HStack(spacing: 12) {
                Image(systemName: "display")
                    .font(.system(size: 18))
                    .foregroundStyle(DS.accent)
                    .frame(width: 38, height: 38)
                    .background(DS.accentSoft, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                VStack(alignment: .leading, spacing: 2) {
                    Text(peer.name).font(.system(.headline))
                    Text(peer.addr).font(.system(.caption, design: .monospaced)).foregroundStyle(DS.textDim)
                }
                Spacer()
                if let fingerprint = peer.fingerprint {
                    FingerprintText(fingerprint: fingerprint)
                } else if peer.paired {
                    Text("paired")
                        .font(.system(.caption, weight: .semibold))
                        .foregroundStyle(DS.ok)
                }
            }
            Divider()
            Text("Move the cursor across the \(model.status?.edge.title.lowercased() ?? "right") edge to take over the other computer, or press the toggle hotkey.")
                .font(.system(.caption))
                .foregroundStyle(DS.textDim)
        }
        .card()
    }

    private var edgeCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            SectionHeader("Cursor exit edge")
            Text("Where the cursor leaves this Mac to control the other computer.")
                .font(.system(.caption))
                .foregroundStyle(DS.textDim)
            HStack(spacing: 8) {
                ForEach(FLEdge.allCases, id: \.self) { edge in
                    let selected = model.status?.edge == edge
                    Button {
                        model.setEdge(edge)
                    } label: {
                        VStack(spacing: 4) {
                            Image(systemName: edge.symbol)
                                .font(.system(size: 16))
                            Text(edge.title).font(.system(.caption2, weight: .medium))
                        }
                        .frame(minWidth: 52)
                        .padding(.vertical, 8)
                        .background(selected ? DS.accentSoft : DS.cardAlt, in: RoundedRectangle(cornerRadius: DS.radiusSmall, style: .continuous))
                        .overlay(
                            RoundedRectangle(cornerRadius: DS.radiusSmall, style: .continuous)
                                .stroke(selected ? DS.accent : DS.border, lineWidth: selected ? 1.5 : 1)
                        )
                    }
                    .buttonStyle(.plain)
                }
            }
            Divider()
            HStack {
                Text("Toggle hotkey").font(.system(.callout)).foregroundStyle(DS.textDim)
                Spacer()
                if let hotkey = model.status?.hotkey {
                    KeyCap(KeyCodes.hotkeyDisplay(modifiers: hotkey.modifiers, usage: hotkey.usage))
                }
                Button("Change") { showHotkeyRecorder = true }
                    .buttonStyle(QuietButtonStyle())
            }
        }
        .frame(maxWidth: .infinity)
        .card()
    }

    private var metricsCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            SectionHeader("Link health")
            if let metrics = model.status?.metrics {
                StatRow(label: "Latency", value: model.isConnected ? String(format: "%.1f ms", metrics.rttMs) : "—", mono: true)
                StatRow(label: "Sent", value: ByteFormat.human(metrics.txBytes), mono: true)
                StatRow(label: "Received", value: ByteFormat.human(metrics.rxBytes), mono: true)
                StatRow(label: "Events", value: "\(metrics.eventsOut) → / \(metrics.eventsIn) ←", mono: true)
                StatRow(label: "Uptime", value: ByteFormat.duration(metrics.uptimeMs), mono: true)
            } else {
                StatRow(label: "Latency", value: "—")
            }
        }
        .frame(maxWidth: .infinity)
        .card()
    }

    private var securityFooter: some View {
        HStack(spacing: 10) {
            Image(systemName: "lock.shield.fill")
                .foregroundStyle(DS.ok)
            Text("Traffic is end-to-end encrypted (TLS 1.3). Only paired computers can send input.")
                .font(.system(.caption))
                .foregroundStyle(DS.textDim)
        }
    }

    // MARK: Client

    private var clientContent: some View {
        VStack(alignment: .leading, spacing: 16) {
            if let peer = model.status?.peer, model.status?.isConnected == true {
                peerCard(peer)
                HStack(alignment: .top, spacing: 16) {
                    metricsCard
                    clientHintCard
                }
                Button("Disconnect") { model.disconnect() }
                    .buttonStyle(SecondaryButtonStyle())
            } else {
                if model.status?.state == "awaiting_pair" {
                    awaitPairingCard
                }
                HStack(alignment: .top, spacing: 16) {
                    discoveredCard
                    connectCard
                }
            }
        }
    }

    private var clientHintCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionHeader("While connected")
            bullet("cursorarrow.motionlines", "Push the cursor over the edge it entered from to send control back.")
            bullet("keyboard", "Your keyboard keeps working locally; local input is never forwarded.")
            bullet("doc.on.clipboard", "Clipboard syncs both ways automatically.")
        }
        .frame(maxWidth: .infinity)
        .card()
    }

    private func bullet(_ symbol: String, _ text: String) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: symbol)
                .font(.system(size: 12))
                .foregroundStyle(DS.accent)
                .frame(width: 16)
            Text(text)
                .font(.system(.caption))
                .foregroundStyle(DS.textDim)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var awaitPairingCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            SectionHeader("Pairing")
            HStack(spacing: 14) {
                Image(systemName: "lock.shield")
                    .font(.system(size: 20))
                    .foregroundStyle(DS.warn)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Enter the code shown on the server")
                        .font(.system(.callout, weight: .semibold))
                    Text("6 digits, expires in two minutes")
                        .font(.system(.caption))
                        .foregroundStyle(DS.textDim)
                }
                Spacer()
                TextField("123 456", text: $manualCode)
                    .textFieldStyle(.plain)
                    .font(.system(.title3, design: .monospaced))
                    .frame(width: 110)
                    .padding(8)
                    .background(DS.cardAlt, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous).stroke(DS.border, lineWidth: 1))
                Button("Pair") {
                    model.submitCode(manualCode)
                    manualCode = ""
                }
                .buttonStyle(PrimaryButtonStyle())
            }
        }
        .card(tint: DS.warnSoft)
    }

    private var discoveredCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                SectionHeader("Found on this network")
                Spacer()
                if !(model.status?.discovered.isEmpty ?? true) {
                    Text("\(model.status?.discovered.count ?? 0)")
                        .font(.system(.caption2, weight: .semibold))
                        .foregroundStyle(DS.textDim)
                }
            }
            let servers = (model.status?.discovered ?? []).filter(\.isServer)
            if servers.isEmpty {
                EmptyHint(
                    symbol: "wifi",
                    title: "No servers found yet",
                    message: "Make sure FlowLink runs in Server mode on the other computer and both are on the same network."
                )
            } else {
                VStack(spacing: 8) {
                    ForEach(servers) { device in
                        HStack(spacing: 10) {
                            Image(systemName: "desktopcomputer")
                                .foregroundStyle(DS.accent)
                                .frame(width: 26)
                            VStack(alignment: .leading, spacing: 1) {
                                Text(device.name).font(.system(.callout, weight: .medium))
                                HStack(spacing: 6) {
                                    Text(device.addr ?? "…")
                                        .font(.system(.caption2, design: .monospaced))
                                        .foregroundStyle(DS.textDim)
                                    if let version = device.version {
                                        Text("v\(version)")
                                            .font(.system(.caption2))
                                            .foregroundStyle(DS.textFaint)
                                    }
                                }
                            }
                            Spacer()
                            Button("Connect") { model.connect(device: device) }
                                .buttonStyle(SecondaryButtonStyle())
                        }
                        .padding(10)
                        .background(DS.cardAlt, in: RoundedRectangle(cornerRadius: DS.radiusSmall, style: .continuous))
                    }
                }
            }

            Divider()
            HStack(spacing: 8) {
                Button {
                    showScanner = true
                } label: {
                    Label("Scan QR code", systemImage: "qrcode.viewfinder")
                }
                .buttonStyle(SecondaryButtonStyle())
                Spacer()
            }
        }
        .frame(maxWidth: .infinity)
        .card()
    }

    private var connectCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            SectionHeader("Connect manually")
            VStack(alignment: .leading, spacing: 6) {
                Text("Address").font(.system(.caption, weight: .medium)).foregroundStyle(DS.textDim)
                HStack(spacing: 6) {
                    TextField("192.168.1.10", text: $manualHost)
                        .textFieldStyle(.plain)
                        .font(.system(.callout, design: .monospaced))
                        .padding(8)
                        .background(DS.cardAlt, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                        .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous).stroke(DS.border, lineWidth: 1))
                    TextField("24800", text: $manualPort)
                        .textFieldStyle(.plain)
                        .font(.system(.callout, design: .monospaced))
                        .frame(width: 64)
                        .padding(8)
                        .background(DS.cardAlt, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                        .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous).stroke(DS.border, lineWidth: 1))
                }
            }
            VStack(alignment: .leading, spacing: 6) {
                Text("Pairing code (optional)").font(.system(.caption, weight: .medium)).foregroundStyle(DS.textDim)
                TextField("123 456", text: $manualCode)
                    .textFieldStyle(.plain)
                    .font(.system(.callout, design: .monospaced))
                    .padding(8)
                    .background(DS.cardAlt, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous).stroke(DS.border, lineWidth: 1))
            }
            Button("Connect") {
                let port = UInt16(manualPort) ?? 24800
                model.connectManual(host: manualHost, port: port, code: manualCode)
                manualCode = ""
            }
            .buttonStyle(PrimaryButtonStyle())
            .disabled(manualHost.trimmingCharacters(in: .whitespaces).isEmpty)

            Divider()
            VStack(alignment: .leading, spacing: 6) {
                Text("Or paste an invite link").font(.system(.caption, weight: .medium)).foregroundStyle(DS.textDim)
                HStack(spacing: 6) {
                    TextField("flowlink://connect?…", text: $pasteLink)
                        .textFieldStyle(.plain)
                        .font(.system(.caption, design: .monospaced))
                        .padding(8)
                        .background(DS.cardAlt, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                        .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous).stroke(DS.border, lineWidth: 1))
                    Button("Open") {
                        model.connectLink(pasteLink)
                        pasteLink = ""
                    }
                    .buttonStyle(SecondaryButtonStyle())
                    .disabled(pasteLink.isEmpty)
                }
            }

            if let known = model.status?.pairedDevices, !known.isEmpty {
                Divider()
                SectionHeader("Known computers")
                VStack(spacing: 8) {
                    ForEach(known) { device in
                        HStack(spacing: 10) {
                            Image(systemName: "clock.arrow.circlepath")
                                .foregroundStyle(DS.textDim)
                                .frame(width: 22)
                            VStack(alignment: .leading, spacing: 1) {
                                Text(device.name).font(.system(.callout, weight: .medium))
                                Text(device.lastAddr ?? "—")
                                    .font(.system(.caption2, design: .monospaced))
                                    .foregroundStyle(DS.textDim)
                            }
                            Spacer()
                            Button("Connect") { model.connect(paired: device) }
                                .buttonStyle(SecondaryButtonStyle())
                        }
                        .padding(8)
                        .background(DS.cardAlt, in: RoundedRectangle(cornerRadius: DS.radiusSmall, style: .continuous))
                    }
                }
            }
        }
        .frame(maxWidth: .infinity)
        .card()
    }

    private func errorCard(_ error: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(DS.err)
            VStack(alignment: .leading, spacing: 2) {
                Text("Something went wrong")
                    .font(.system(.callout, weight: .semibold))
                Text(error)
                    .font(.system(.caption))
                    .foregroundStyle(DS.textDim)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer()
        }
        .padding(12)
        .background(DS.errSoft, in: RoundedRectangle(cornerRadius: DS.radiusSmall, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: DS.radiusSmall, style: .continuous)
                .stroke(DS.err.opacity(0.35), lineWidth: 1)
        )
    }
}
