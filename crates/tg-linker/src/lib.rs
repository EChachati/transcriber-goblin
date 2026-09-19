//! tg-linker: algoritmo puro de linking (wikilinks) y keywords deterministas.
//!
//! Port de `server/app/linker.py` con dos mejoras:
//! - El contenido se tokeniza **una vez** por doc (el Python lo re-escaneaba
//!   por cada variante: ahí estaba el O(N·M·V)).
//! - Los "auto" se devuelven como **ediciones** (start/end/replacement en
//!   offsets de bytes) en vez de mutar el string: la fase 2 las aplicará por
//!   diffs sobre el `yrs::Doc` en memoria (sin el `clear()+insert`).

pub mod keywords;
pub mod linker;
pub mod normalize;

pub use linker::{
    expand_variants, link_doc, AutoLink, DocLinkResult, LinkEdit, NoteSpec, Proposal,
};
pub use normalize::{normalize, tokenize_with_positions, Token};
