mod audio;

use std::env;
use std::process::ExitCode;

use anyhow::{Context, Result};

use whisper_rs::{FullParams, SamplingStrategy, WhisperContext, WhisperContextParameters};

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(err) => {
            eprintln!("error: {err:#}");
            ExitCode::FAILURE
        }
    }
}

fn run() -> Result<()> {
    let args: Vec<String> = env::args().collect();
    if args.len() < 3 {
        eprintln!("uso: tg-transcriber stt --model <ruta-modelo> [--language es] [--threads N] <audio.wav>");
        eprintln!("ej:  tg-transcriber stt --model models/ggml-small-q5_1.bin samples/jfk.wav");
        return Ok(());
    }

    let mut model_path = None;
    let mut wav_path = None;
    let mut language: Option<String> = None;
    let mut threads: i32 = 4;

    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--model" => {
                i += 1;
                model_path = Some(args.get(i).context("--model sin valor")?.clone());
            }
            "--language" => {
                i += 1;
                language = Some(args.get(i).context("--language sin valor")?.clone());
            }
            "--threads" => {
                i += 1;
                threads = args
                    .get(i)
                    .context("--threads sin valor")?
                    .parse()
                    .context("--threads debe ser entero")?;
            }
            a if a.starts_with('-') => anyhow::bail!("argumento desconocido: {a}"),
            a => wav_path = Some(a.to_string()),
        }
        i += 1;
    }

    let model_path = model_path.context("falta --model")?;
    let wav_path = wav_path.context("falta <audio.wav>")?;
    let samples =
        read_wav_s16(&wav_path).context("no se pudo leer el wav (esperaba PCM s16, 16 kHz)")?;

    let ctx_params = WhisperContextParameters::default();
    let ctx = WhisperContext::new_with_params(&model_path, ctx_params)
        .with_context(|| format!("cargando modelo '{model_path}'"))?;

    let mut state = ctx
        .create_state()
        .context("creando estado de decodificado")?;

    let mut params = FullParams::new(SamplingStrategy::Greedy { best_of: 1 });
    if let Some(lang) = language.as_deref() {
        params.set_language(Some(lang));
    }
    params.set_n_threads(threads.max(1));
    params.set_translate(false);
    params.set_no_context(true);

    state
        .full(params, &samples)
        .context("fallo al transcribir")?;

    let n = state.full_n_segments();
    let mut out = Vec::with_capacity(n.max(0) as usize);
    for idx in 0..n {
        let seg = state
            .get_segment(idx)
            .ok_or_else(|| anyhow::anyhow!("segmento {idx} fuera de rango"))?;
        let t0 = seg.start_timestamp() as f32 / 100.0;
        let t1 = seg.end_timestamp() as f32 / 100.0;
        let text = seg.to_str_lossy()?.to_string();
        out.push(Segment {
            start: t0,
            end: t1,
            text,
        });
    }

    let json = serde_json::to_string_pretty(&out).context("serializando")?;
    println!("{json}");
    eprintln!(
        "# {n} segmentos, {:.1}s audio",
        samples.len() as f32 / 16000.0
    );
    Ok(())
}

#[derive(serde::Serialize)]
struct Segment {
    start: f32,
    end: f32,
    text: String,
}

/// Lee un WAV PCM 16-bit (mono o estéreo) a f32 en [-1, 1] a 16 kHz.
fn read_wav_s16(path: &str) -> Result<Vec<f32>> {
    let data = std::fs::read(path).with_context(|| format!("leyendo '{path}'"))?;
    if data.len() < 44 || &data[0..4] != b"RIFF" || &data[8..12] != b"WAVE" {
        anyhow::bail!("'{path}' no es un RIFF/WAVE válido");
    }
    let channels = u16::from_le_bytes([data[22], data[23]]) as usize;
    let sample_rate = u32::from_le_bytes([data[24], data[25], data[26], data[27]]) as usize;
    let bits = u16::from_le_bytes([data[34], data[35]]);

    if sample_rate != 16000 {
        eprintln!("aviso: sample_rate={sample_rate}; se esperaba 16000");
    }
    if bits != 16 {
        anyhow::bail!("bits={bits}; solo se soporta PCM s16 por ahora");
    }
    let data_start = {
        let mut off = 12usize;
        loop {
            if off + 8 > data.len() {
                anyhow::bail!("RIFF truncado buscando 'data'");
            }
            let chunk = &data[off..off + 4];
            let size =
                u32::from_le_bytes([data[off + 4], data[off + 5], data[off + 6], data[off + 7]])
                    as usize;
            if chunk == b"data" {
                break off + 8;
            }
            off += 8 + size + (size & 1);
        }
    };

    let mut samples = Vec::with_capacity((data.len() - data_start) / 2 / channels);
    let mut i = data_start;
    while i + 2 <= data.len() {
        let sample = i16::from_le_bytes([data[i], data[i + 1]]) as f32 / 32768.0;
        if channels == 1 || (i - data_start) / 2 % channels == 0 {
            samples.push(sample);
        }
        i += 2;
    }
    Ok(samples)
}
