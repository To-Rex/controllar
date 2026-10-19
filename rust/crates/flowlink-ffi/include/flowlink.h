/*
 * FlowLink — C ABI for the Rust core.
 *
 * Threading contract:
 *  - All flowlink_* functions are callable from any thread and return
 *    promptly (commands are queued inside the core).
 *  - Host callbacks fire on core runtime threads; marshal to your main
 *    thread as needed.
 *  - C strings passed INTO callbacks are only valid during the call — copy.
 *  - Strings returned BY flowlink_*_json / flowlink_invite_link /
 *    flowlink_logs_dir / flowlink_tail_logs and byte buffers returned by
 *    flowlink_pairing_qr_matrix are heap-allocated and must be released
 *    with flowlink_free_string / flowlink_free_bytes.
 *
 * Layout note: FlHostInput (48 bytes) and FlInputEvent (32 bytes) are
 * #[repr(C)] structs on the Rust side with compile-time size assertions;
 * keep the C definitions below in sync.
 */

#ifndef FLOWLINK_H
#define FLOWLINK_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ------------------------------------------------------------------ */
/* Opaque handle                                                       */
/* ------------------------------------------------------------------ */

typedef struct FlCore FlCore;

/* ------------------------------------------------------------------ */
/* Constants                                                           */
/* ------------------------------------------------------------------ */

/* Modes for flowlink_set_mode */
enum {
    FL_MODE_CLIENT = 0,
    FL_MODE_SERVER = 1
};

/* Kinds for FlHostInput (shell -> core) */
enum {
    FL_HOST_MOUSE_MOVE = 0,
    FL_HOST_KEY = 1,
    FL_HOST_MOUSE_BUTTON = 2,
    FL_HOST_SCROLL = 3
};

/* Kinds for FlInputEvent (core -> shell, for injection) */
enum {
    FL_INPUT_ENTER = 0,
    FL_INPUT_KEY = 1,
    FL_INPUT_MOUSE_MOVE = 2,
    FL_INPUT_MOUSE_BUTTON = 3,
    FL_INPUT_SCROLL = 4,
    FL_INPUT_RELEASE_ALL = 5
};

/* Modifier bits (FlHostInput.modifiers / FlInputEvent.modifiers) */
enum {
    FL_MOD_SHIFT = 1,
    FL_MOD_CTRL = 2,
    FL_MOD_ALT = 4,
    FL_MOD_META = 8
};

/* Edges (FlInputEvent.flags for FL_INPUT_ENTER; flowlink_set_edge) */
enum {
    FL_EDGE_LEFT = 0,
    FL_EDGE_RIGHT = 1,
    FL_EDGE_TOP = 2,
    FL_EDGE_BOTTOM = 3
};

/* Mouse button values in FlInputEvent.usage (HID button page) */
enum {
    FL_MOUSE_BUTTON_LEFT = 1,
    FL_MOUSE_BUTTON_RIGHT = 2,
    FL_MOUSE_BUTTON_MIDDLE = 4
};

/* ------------------------------------------------------------------ */
/* Structs                                                             */
/* ------------------------------------------------------------------ */

/** Input captured by the native shell. */
typedef struct FlHostInput {
    int32_t kind;        /* FL_HOST_* */
    uint16_t usage;      /* HID key usage id, or mouse button */
    uint8_t down;        /* key/button pressed */
    uint8_t modifiers;   /* FL_MOD_* bitmask */
    double x;            /* MouseMove: absolute x px; Scroll: dx; Key: unused */
    double y;            /* MouseMove: absolute y px; Scroll: dy; Key: unused */
    double dx;           /* MouseMove: delta x px */
    double dy;           /* MouseMove: delta y px */
    int32_t flags;       /* bit 0: precise scroll */
} FlHostInput;

/** Input event for the shell to inject. */
typedef struct FlInputEvent {
    int32_t kind;        /* FL_INPUT_* */
    uint16_t usage;      /* HID key usage id, or mouse button */
    uint8_t down;        /* key/button pressed */
    uint8_t modifiers;   /* FL_MOD_* bitmask */
    double x;            /* Enter: along-edge 0..1; MouseMove/Scroll: dx */
    double y;            /* MouseMove/Scroll: dy; Enter: unused */
    int32_t flags;       /* Enter: FL_EDGE_*; Scroll: bit 0 precise */
} FlInputEvent;

/** Callbacks implemented by the host shell (all optional). */
typedef struct FlHostCallbacks {
    void *user_data;
    /** Status snapshot JSON. */
    void (*on_status)(void *user_data, const char *status_json);
    /** Input event to inject locally (client role). */
    void (*on_input)(void *user_data, const FlInputEvent *event);
    /** Clipboard text received from the peer (UTF-8). */
    void (*on_clipboard)(void *user_data, const char *utf8_text);
    /** Notice JSON: {"kind": "...", "message": "..."} */
    void (*on_notice)(void *user_data, const char *notice_json);
    /** Called exactly once when the core releases user_data. */
    void (*on_release)(void *user_data);
} FlHostCallbacks;

/* ------------------------------------------------------------------ */
/* Lifecycle                                                           */
/* ------------------------------------------------------------------ */

/** Core version (static string; do not free). */
const char *flowlink_version(void);

/**
 * Create the engine.
 * @param config_dir  UTF-8 path for config/logs; NULL = platform default.
 * @param callbacks   may be NULL; must stay valid until flowlink_free().
 * @return handle, or NULL on fatal setup error.
 */
FlCore *flowlink_new(const char *config_dir, const FlHostCallbacks *callbacks);

/** Tear down the engine and free the handle. Safe with NULL. */
void flowlink_free(FlCore *core);

/* ------------------------------------------------------------------ */
/* Commands                                                            */
/* ------------------------------------------------------------------ */

/** Start running in the configured mode. */
void flowlink_start(FlCore *core);

/** Stop running (listener/dialer); the engine handle stays alive. */
void flowlink_stop(FlCore *core);

/** FL_MODE_CLIENT / FL_MODE_SERVER; (re)starts. Returns 0 or -1. */
int32_t flowlink_set_mode(FlCore *core, int32_t mode);

/**
 * Connect to a server (client role).
 * @param pairing_code        optional 6-digit code (NULL if the user will type it)
 * @param expected_fingerprint optional pinned certificate fingerprint
 * @return 0 queued, -1 invalid arguments.
 */
int32_t flowlink_connect(FlCore *core, const char *host, uint16_t port,
                         const char *pairing_code,
                         const char *expected_fingerprint);

/** Connect using a `flowlink://connect?...` link (QR content / pasted text). */
int32_t flowlink_connect_link(FlCore *core, const char *link);

/** Stop the client loop; stay idle. */
void flowlink_disconnect(FlCore *core);

/** Deliver the pairing code typed by the user (client role). */
void flowlink_submit_pair_code(FlCore *core, const char *code);

/** Invalidate the current pairing code and show a fresh one (server role). */
void flowlink_regenerate_pair_code(FlCore *core);

/** Remove a paired device from memory. */
void flowlink_forget_device(FlCore *core, const char *device_id);

/** Feed input captured by the shell. */
void flowlink_send_input(FlCore *core, const FlHostInput *event);

/** Client role: user pushed the return edge — ask the server for control. */
void flowlink_request_control(FlCore *core);

/** Server role: toggle driving the remote device (UI/menu bar button). */
void flowlink_toggle_control(FlCore *core);

/** Local clipboard text changed. */
void flowlink_set_clipboard_text(FlCore *core, const char *utf8_text);

/** Rename this device. */
void flowlink_set_device_name(FlCore *core, const char *name);

/**
 * Report whether the shell's local input source (event tap / hook) is
 * capturing. active = 0 makes the core log a warning and show a notice with
 * `reason` (may be null).
 */
void flowlink_set_input_capture(FlCore *core, int active, const char *reason);

/** Local edge the cursor exits through (server role): FL_EDGE_*. */
void flowlink_set_edge(FlCore *core, int32_t edge);

/** Enable/disable clipboard synchronisation. */
void flowlink_set_clipboard_enabled(FlCore *core, int32_t enabled);

/**
 * Set the toggle hotkey:
 * {"ctrl":bool,"alt":bool,"shift":bool,"meta":bool,"usage":u16}
 * Returns 0 on success, -1 on parse error.
 */
int32_t flowlink_set_hotkey(FlCore *core, const char *hotkey_json);

/** TCP listen port (server role); restarts the listener when running. */
void flowlink_set_listen_port(FlCore *core, uint16_t port);

/** Report local screen size in pixels (edge math). */
void flowlink_set_local_screen(FlCore *core, double width, double height,
                               double scale);

/* ------------------------------------------------------------------ */
/* Queries (free the results!)                                         */
/* ------------------------------------------------------------------ */

/** Status snapshot JSON. */
char *flowlink_status_json(FlCore *core);

/** Configuration JSON. */
char *flowlink_config_json(FlCore *core);

/** Invite link text for the current pairing session. */
char *flowlink_invite_link(FlCore *core);

/**
 * QR matrix of the invite link: size*size bytes, row-major, 1 = dark.
 * @param out_size receives the matrix size.
 */
uint8_t *flowlink_pairing_qr_matrix(FlCore *core, int32_t *out_size);

/** Absolute path of the logs directory. */
char *flowlink_logs_dir(FlCore *core);

/** Last max_lines lines of the newest log file, joined by '\n'. */
char *flowlink_tail_logs(FlCore *core, int32_t max_lines);

/** Free a string returned by this library. */
void flowlink_free_string(char *value);

/** Free a byte buffer returned by this library (size = matrix size²). */
void flowlink_free_bytes(uint8_t *value, size_t len);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* FLOWLINK_H */
