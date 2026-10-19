//! Local-network discovery over mDNS/DNS-SD.
//!
//! Servers announce `_flowlink._tcp.local.` with TXT metadata (device id,
//! name, role, version, certificate fingerprint); every engine browses for
//! that service type. No external dependencies (Bonjour/Avahi) are required:
//! `mdns-sd` implements multicast DNS in pure Rust on all platforms.

use std::collections::HashMap;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::Duration;

use mdns_sd::{ServiceDaemon, ServiceEvent, ServiceInfo};
use tokio_util::sync::CancellationToken;

use crate::engine::Engine;
use crate::types::{DeviceInfo, Mode};
use crate::util;

/// The DNS-SD service type.
pub const SERVICE_TYPE: &str = "_flowlink._tcp.local.";

#[derive(Clone, Debug, PartialEq)]
struct AnnounceState {
    fullname: String,
    id: String,
    name: String,
    port: u16,
    fingerprint: String,
}

/// Long-running discovery task: browses for peers and announces this device
/// while it is a listening server.
pub async fn discovery_task(engine: Arc<Engine>, cancel: CancellationToken) {
    let daemon = match ServiceDaemon::new() {
        Ok(daemon) => daemon,
        Err(err) => {
            tracing::warn!("local discovery disabled: {err}");
            return;
        }
    };
    let receiver = match daemon.browse(SERVICE_TYPE) {
        Ok(receiver) => receiver,
        Err(err) => {
            tracing::warn!("local discovery browse failed: {err}");
            return;
        }
    };

    let mut announced: Option<AnnounceState> = None;
    let mut tick = tokio::time::interval(Duration::from_secs(2));
    tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);

    loop {
        tokio::select! {
            _ = cancel.cancelled() => break,
            event = receiver.recv_async() => {
                match event {
                    Ok(ServiceEvent::ServiceResolved(info)) => {
                        if let Some(device) = device_from_info(&info) {
                            if device.id != engine.own_device_id() {
                                tracing::debug!(device = %device.name, addr = ?device.addr, "peer discovered");
                                // Scope the guard: `refresh_devices_status`
                                // takes the same non-reentrant lock.
                                {
                                    engine.devices.lock().insert(device.id.clone(), device);
                                }
                                engine.refresh_devices_status();
                            }
                        }
                    }
                    Ok(ServiceEvent::ServiceRemoved(_, fullname)) => {
                        tracing::debug!(%fullname, "service removed");
                        // Pruning happens on the engine tick; nothing to do.
                    }
                    Ok(_) => {}
                    Err(err) => {
                        tracing::debug!("discovery event error: {err}");
                        tokio::time::sleep(Duration::from_secs(1)).await;
                    }
                }
            }
            _ = tick.tick() => {
                let desired = desired_announce(&engine);
                match (&announced, &desired) {
                    (Some(current), Some(next)) if current.name == next.name
                        && current.port == next.port
                        && current.fingerprint == next.fingerprint => {}
                    (Some(current), _) => {
                        daemon.unregister(&current.fullname).ok();
                        announced = None;
                    }
                    _ => {}
                }
                if announced.is_none() {
                    if let Some(next) = desired {
                        // Re-derive the full name after re-registering.
                        if let Some(state) = register(&daemon, &next) {
                            tracing::info!(service = %state.fullname, "announcing on the local network");
                            announced = Some(state);
                        }
                    }
                }
            }
        }
    }

    if let Some(state) = announced {
        daemon.unregister(&state.fullname).ok();
    }
    daemon.shutdown().ok();
    tracing::debug!("discovery task stopped");
}

/// What this device wants to announce right now (servers only, while
/// listening).
fn desired_announce(engine: &Arc<Engine>) -> Option<AnnounceState> {
    if engine.cfg.lock().mode != Mode::Server || !engine.listening.load(Ordering::Relaxed) {
        return None;
    }
    let cfg = engine.cfg.lock().clone();
    Some(AnnounceState {
        fullname: String::new(), // filled by register()
        id: cfg.device_id,
        name: cfg.device_name,
        port: engine.effective_listen_port(),
        fingerprint: engine.identity.fingerprint.clone(),
    })
}

fn register(daemon: &ServiceDaemon, desired: &AnnounceState) -> Option<AnnounceState> {
    let ip = util::preferred_lan_addr()?;
    let short = &desired.id[..8.min(desired.id.len())];
    let instance = format!("{} [{}]", desired.name, short);
    let host = format!("flowlink-{short}.local.");

    let mut props: HashMap<String, String> = HashMap::new();
    props.insert("name".into(), desired.name.clone());
    props.insert("fp".into(), desired.fingerprint.clone());
    props.insert("ver".into(), crate::VERSION.into());
    props.insert("role".into(), Mode::Server.as_str().into());
    props.insert("id".into(), desired.id.clone());

    let info = ServiceInfo::new(
        SERVICE_TYPE,
        &instance,
        &host,
        ip.as_str(),
        desired.port,
        props,
    )
    .ok()?;
    let fullname = info.get_fullname().to_string();
    daemon.register(info).ok()?;

    Some(AnnounceState {
        fullname,
        id: desired.id.clone(),
        name: desired.name.clone(),
        port: desired.port,
        fingerprint: desired.fingerprint.clone(),
    })
}

/// Extract a [`DeviceInfo`] from a resolved mDNS service.
fn device_from_info(info: &ServiceInfo) -> Option<DeviceInfo> {
    let id = info.get_property_val_str("id")?.to_string();
    let name = info
        .get_property_val_str("name")
        .unwrap_or("FlowLink device")
        .to_string();
    let fingerprint = info.get_property_val_str("fp").unwrap_or("").to_string();
    let version = info.get_property_val_str("ver").map(|v| v.to_string());
    let role = info.get_property_val_str("role").and_then(Mode::parse);
    let port = info.get_port();
    let addr = pick_address(info);
    Some(DeviceInfo {
        id,
        name,
        fingerprint,
        addr,
        port,
        role,
        version,
        last_seen_ms: util::now_ms(),
    })
}

/// Prefer a private IPv4 address for the peer.
fn pick_address(info: &ServiceInfo) -> Option<String> {
    let mut fallback: Option<String> = None;
    for addr in info.get_addresses() {
        match addr {
            std::net::IpAddr::V4(v4) if !v4.is_loopback() => {
                if v4.is_private() {
                    return Some(v4.to_string());
                }
                fallback.get_or_insert_with(|| v4.to_string());
            }
            std::net::IpAddr::V4(_) => {}
            std::net::IpAddr::V6(v6) => {
                if !v6.is_loopback() && !v6.is_unicast_link_local() {
                    fallback.get_or_insert_with(|| v6.to_string());
                }
            }
        }
    }
    fallback
}
