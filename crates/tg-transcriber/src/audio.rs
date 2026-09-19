//! Trait `AudioSource`: abstracción de captura (draft de la fase 0).
//!
//! Backend mínimo implementado: **ParecBackend** (parec de PulseAudio/PipeWire,
//! con el que se resolvió el bug de los monitores — ver `HANDOFF.md`).
//! Un futuro `WasmapiBackend` (Windows) implementará el mismo trait.
#![allow(dead_code)] // draft de la fase 0; se usará en la fase 3

use std::path::PathBuf;

/// Fuente de audio que captura durante una sesión y entrega un WAV PCM s16 mono 16 kHz.
pub trait AudioSource {
    /// Etiqueta del hablante: "TU" o "REMOTO".
    fn label(&self) -> &'static str;
    /// Nombre lógico de la fuente (p. ej. id de pipewire).
    fn device(&self) -> &str;
    /// Entrega un WAV PCM s16le mono 16 kHz ya guardado en disco.
    fn wav_path(&self) -> &PathBuf;
    /// Arranca la captura (bloquea solo hasta confirmada, no durante la sesión).
    fn start(&mut self) -> Result<(), String>;
    /// Detiene la captura y finaliza el WAV. Devuelve duración en segundos.
    fn stop(&mut self) -> Result<f64, String>;
    /// Duración aproximada capturada hasta ahora (para la CLI en vivo).
    fn duration(&self) -> f64;
    /// Segundos desde el inicio; se usa para etiquetar el stream.
    fn started_at(&self) -> std::time::SystemTime;
}

impl<T: AudioSource + ?Sized> AudioSource for Box<T> {
    fn label(&self) -> &'static str {
        (**self).label()
    }
    fn device(&self) -> &str {
        (**self).device()
    }
    fn wav_path(&self) -> &PathBuf {
        (**self).wav_path()
    }
    fn start(&mut self) -> Result<(), String> {
        (**self).start()
    }
    fn stop(&mut self) -> Result<f64, String> {
        (**self).stop()
    }
    fn duration(&self) -> f64 {
        (**self).duration()
    }
    fn started_at(&self) -> std::time::SystemTime {
        (**self).started_at()
    }
}

// TODO(fase 3): implementación `ParecBackend` (spawn `parec -d <dev> ...`,
// escritura .raw y envoltura WAV al detener, equivalente a `capture.py`).
