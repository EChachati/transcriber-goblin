//! Benchmark del linker (tg-linker, fase 1) — espejo del `linker_bench.py`.
//!
//! Misma generación de datos que el baseline Python (N docs, cada una con
//! menciones a las `refs_each` notas anteriores) pero midiendo solo el núcleo
//! puro (sin I/O). Para comparar con:
//!   N=20: 1209 ms · N=40: 5804 ms · N=80: 25582 ms · N=160: 105135 ms
//!
//! Uso: cargo run --release -p tg-linker --example bench

use std::time::Instant;

use tg_linker::{link_doc, NoteSpec};

const TITLE: &str = "Nota iterativa {i} sobre despliegue servidor y migracion de datos";

fn seed(n: usize, refs_each: usize) -> Vec<(NoteSpec, String)> {
    (0..n)
        .map(|i| {
            let title = TITLE.replace("{i}", &i.to_string());
            let others = (i.saturating_sub(refs_each)..i)
                .map(|j| TITLE.replace("{i}", &j.to_string()))
                .collect::<Vec<_>>();
            let mut body = format!("# {title}\n");
            for t in &others {
                body.push_str(&format!(
                    "Se hablo de {t} con el equipo y se decidio migrar {title}.\n"
                ));
            }
            let note = NoteSpec {
                id: format!("doc_{i:016x}"),
                title,
                aliases: vec![],
            };
            (note, body)
        })
        .collect()
}

fn bench(n: usize, refs_each: usize) {
    let data = seed(n, refs_each);
    let notes: Vec<NoteSpec> = data.iter().map(|(note, _)| note.clone()).collect();
    let t0 = Instant::now();
    let mut auto = 0usize;
    let mut proposals = 0usize;
    for (note, content) in &data {
        let res = link_doc(content, &note.id, &notes);
        auto += res.auto.len();
        proposals += res.proposals.len();
    }
    let ms = t0.elapsed().as_secs_f64() * 1000.0;
    println!("N={n:>4} refs={refs_each}  {ms:8.1} ms   auto={auto} propuestas={proposals}");
}

fn main() {
    println!("benchmark linker (Rust, núcleo puro)");
    for n in [20usize, 40, 80, 160] {
        bench(n, 10);
    }
}
