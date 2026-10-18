import SwiftUI

/// First-run experience: welcome → mode → permissions.
public struct OnboardingView: View {
    @ObservedObject var model: AppModel

    @State private var step = 0
    @State private var name: String = ""
    @State private var mode: FLMode = .server
    @State private var goingForward = true

    public init(model: AppModel) {
        self.model = model
    }

    public var body: some View {
        VStack(spacing: 0) {
            hero
            Group {
                switch step {
                case 0: welcomeStep
                case 1: modeStep
                default: permissionsStep
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            footer
        }
        .background(DS.bg)
        .onAppear {
            if name.isEmpty { name = model.status?.local.name ?? "" }
        }
    }

    // MARK: Hero

    private var hero: some View {
        VStack(spacing: 10) {
            HStack(spacing: 12) {
                GlyphBadge(size: 44)
                VStack(alignment: .leading, spacing: 2) {
                    Text("FlowLink")
                        .font(.system(size: 26, weight: .bold, design: .rounded))
                    Text("One keyboard. Every computer.")
                        .font(.system(.callout))
                        .foregroundStyle(DS.textDim)
                }
                Spacer()
            }
            .padding(.horizontal, 28)
            .padding(.top, 26)

            HStack(spacing: 6) {
                ForEach(0..<3, id: \.self) { index in
                    Capsule()
                        .fill(index <= step ? DS.accent : DS.border)
                        .frame(width: index == step ? 26 : 12, height: 4)
                        .animation(.spring(response: 0.35, dampingFraction: 0.85), value: step)
                }
            }
            .padding(.top, 4)
            .padding(.leading, 28)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    // MARK: Steps

    private var welcomeStep: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Welcome")
                .font(.system(.title2, weight: .semibold))
            Text("FlowLink shares a keyboard, mouse and clipboard between your Mac and Windows PCs over your local network — like one computer with several screens.")
                .font(.system(.callout))
                .foregroundStyle(DS.textDim)
                .fixedSize(horizontal: false, vertical: true)

            VStack(alignment: .leading, spacing: 8) {
                SectionHeader("Computer name")
                TextField("e.g. Studio Mac", text: $name)
                    .textFieldStyle(.plain)
                    .font(.system(.body))
                    .padding(10)
                    .background(DS.card, in: RoundedRectangle(cornerRadius: DS.radiusSmall, style: .continuous))
                    .overlay(
                        RoundedRectangle(cornerRadius: DS.radiusSmall, style: .continuous)
                            .stroke(DS.border, lineWidth: 1)
                    )
                Text("Visible to your other computers when pairing.")
                    .font(.system(.caption))
                    .foregroundStyle(DS.textFaint)
            }

            featureRow("bolt.fill", "Low latency", "Coalesced events over an encrypted TCP+TLS channel.")
            featureRow("lock.shield.fill", "Secure by design", "Device fingerprints pinned, one-time pairing codes.")
            featureRow("doc.on.clipboard", "Clipboard sync", "Copy here, paste there — including emoji.")
        }
        .padding(28)
        .padding(.top, 8)
    }

    private var modeStep: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("How will this computer be used?")
                .font(.system(.title2, weight: .semibold))
            Text("You can switch at any time in Settings.")
                .font(.system(.callout))
                .foregroundStyle(DS.textDim)

            HStack(spacing: 14) {
                modeCard(
                    .server,
                    symbol: "keyboard",
                    title: "Server",
                    subtitle: "This computer drives the others",
                    detail: "Its keyboard and mouse control the client when the cursor crosses the chosen screen edge."
                )
                modeCard(
                    .client,
                    symbol: "display",
                    title: "Client",
                    subtitle: "This computer is controlled",
                    detail: "It receives input from the server and injects it locally, and can push the cursor back to return control."
                )
            }
        }
        .padding(28)
        .padding(.top, 8)
    }

    private var permissionsStep: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Permissions")
                .font(.system(.title2, weight: .semibold))
            Text("macOS requires explicit consent before an app may capture or inject input events. FlowLink cannot work without these.")
                .font(.system(.callout))
                .foregroundStyle(DS.textDim)
                .fixedSize(horizontal: false, vertical: true)

            PermissionRow(
                permissions: model.permissions,
                symbol: "accessibility",
                title: "Accessibility",
                detail: "Capture and inject keyboard & mouse events, and detect the screen edge.",
                granted: model.permissions.accessibility,
                action: { model.permissions.requestAccessibility() }
            )
            PermissionRow(
                permissions: model.permissions,
                symbol: "mic.badge.plus",
                title: "Input Monitoring",
                detail: "Observe raw keyboard events so they can be relayed to the other computer.",
                granted: model.permissions.inputMonitoring,
                action: { model.permissions.requestInputMonitoring() }
            )

            if !model.permissions.allGranted {
                HStack(spacing: 8) {
                    Image(systemName: "info.circle")
                        .foregroundStyle(DS.warn)
                    Text("After granting, you may need to come back — FlowLink detects it automatically.")
                        .font(.system(.caption))
                        .foregroundStyle(DS.textDim)
                }
            }
        }
        .padding(28)
        .padding(.top, 8)
    }

    // MARK: Footer

    private var footer: some View {
        HStack {
            if step > 0 {
                Button("Back") {
                    goingForward = false
                    withAnimation(.easeInOut(duration: 0.22)) { step -= 1 }
                }
                .buttonStyle(SecondaryButtonStyle())
            }
            Spacer()
            Text(step == 2 ? "You can change everything later in Settings." : " ")
                .font(.system(.caption))
                .foregroundStyle(DS.textFaint)
            Spacer()
            Button(step == 2 ? "Finish setup" : "Continue") {
                if step == 2 {
                    model.completeOnboarding(mode: mode, name: name)
                } else {
                    goingForward = true
                    withAnimation(.easeInOut(duration: 0.22)) { step += 1 }
                }
            }
            .buttonStyle(PrimaryButtonStyle())
        }
        .padding(20)
        .background(DS.card)
        .overlay(Rectangle().frame(height: 1).foregroundStyle(DS.border), alignment: .top)
    }

    // MARK: Bits

    private func featureRow(_ symbol: String, _ title: String, _ detail: String) -> some View {
        HStack(spacing: 12) {
            Image(systemName: symbol)
                .font(.system(size: 15, weight: .medium))
                .foregroundStyle(DS.accent)
                .frame(width: 30, height: 30)
                .background(DS.accentSoft, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.system(.callout, weight: .semibold))
                Text(detail).font(.system(.caption)).foregroundStyle(DS.textDim)
            }
        }
    }

    private func modeCard(
        _ value: FLMode,
        symbol: String,
        title: String,
        subtitle: String,
        detail: String
    ) -> some View {
        let selected = mode == value
        return Button {
            mode = value
        } label: {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Image(systemName: symbol)
                        .font(.system(size: 20, weight: .medium))
                        .foregroundStyle(selected ? DS.accent : DS.textDim)
                    Spacer()
                    Image(systemName: selected ? "checkmark.circle.fill" : "circle")
                        .foregroundStyle(selected ? DS.accent : DS.border)
                }
                Text(title).font(.system(.headline))
                Text(subtitle)
                    .font(.system(.callout, weight: .medium))
                    .foregroundStyle(DS.text)
                Text(detail)
                    .font(.system(.caption))
                    .foregroundStyle(DS.textDim)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(16)
            .background(selected ? DS.accentSoft : DS.card, in: RoundedRectangle(cornerRadius: DS.radius, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: DS.radius, style: .continuous)
                    .stroke(selected ? DS.accent : DS.border, lineWidth: selected ? 1.6 : 1)
            )
            .contentShape(RoundedRectangle(cornerRadius: DS.radius, style: .continuous))
        }
        .buttonStyle(.plain)
    }
}

/// Rounded-square app glyph used in the onboarding hero and sidebar.
public struct GlyphBadge: View {
    public let size: CGFloat

    public init(size: CGFloat) {
        self.size = size
    }

    public var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: size * 0.28, style: .continuous)
                .fill(
                    LinearGradient(
                        colors: [Color(light: 0x6E5BFF, dark: 0x7C6BFF), Color(light: 0x9C5BFF, dark: 0xA97CFF)],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )
                )
            Image(systemName: "arrow.left.arrow.right")
                .font(.system(size: size * 0.42, weight: .bold))
                .foregroundStyle(.white)
        }
        .frame(width: size, height: size)
    }
}

/// A permission status row with a grant button.
public struct PermissionRow: View {
    @ObservedObject var permissions: Permissions
    let symbol: String
    let title: String
    let detail: String
    let granted: Bool
    let action: () -> Void

    public var body: some View {
        HStack(spacing: 12) {
            Image(systemName: granted ? "checkmark.shield.fill" : symbol)
                .font(.system(size: 16, weight: .medium))
                .foregroundStyle(granted ? DS.ok : DS.warn)
                .frame(width: 30, height: 30)
                .background((granted ? DS.okSoft : DS.warnSoft), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.system(.callout, weight: .semibold))
                Text(detail)
                    .font(.system(.caption))
                    .foregroundStyle(DS.textDim)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer()
            if granted {
                Text("Granted")
                    .font(.system(.caption, weight: .semibold))
                    .foregroundStyle(DS.ok)
            } else {
                Button("Grant", action: action)
                    .buttonStyle(SecondaryButtonStyle())
            }
        }
        .padding(12)
        .background(DS.card, in: RoundedRectangle(cornerRadius: DS.radiusSmall, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: DS.radiusSmall, style: .continuous)
                .stroke(granted ? DS.ok.opacity(0.35) : DS.border, lineWidth: 1)
        )
    }
}
