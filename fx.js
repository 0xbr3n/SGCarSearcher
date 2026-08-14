/* ============================================================
   fx.js — optional "Full FX" layer: synthesized sound effects and
   comic burst animations. Runs entirely independently of the main
   app script in index.html — if this file fails to load, nothing
   else in the app breaks (it's a <script defer> add-on).

   No audio/image files are shipped or fetched: every sound here is
   synthesized at runtime with the Web Audio API, so there's nothing
   to host, nothing that can 404, and nothing licensed. Browsers
   block audio until a user gesture; every sound call here happens
   inside a click handler, so that's satisfied automatically.
   ============================================================ */
(function(){
"use strict";
var KEY = "cs4_fx";
var $ = function(id){ return document.getElementById(id) };

/* ---------- state ---------- */
function isOn(){ try{ return localStorage.getItem(KEY) === "1" }catch(e){ return false } }
function setOn(v){
  try{ localStorage.setItem(KEY, v ? "1" : "0") }catch(e){}
  document.body.classList.toggle("fx-on", v);
  var sw = $("fxSwitch");
  if(sw) sw.setAttribute("aria-pressed", v ? "true" : "false");
}

/* ---------- lazy audio context (created on first real user gesture) ---------- */
var ctx = null;
function ac(){
  if(ctx) return ctx;
  var C = window.AudioContext || window.webkitAudioContext;
  if(!C) return null;
  ctx = new C();
  return ctx;
}
function resumeIfNeeded(){ var c=ac(); if(c && c.state==="suspended") c.resume().catch(function(){}); }

/* Short synthesized tone: freq sweep, chosen wave, quick envelope. */
function tone(f0, f1, dur, type, peak){
  var c = ac(); if(!c) return;
  var osc = c.createOscillator(), gain = c.createGain();
  osc.type = type || "sine";
  osc.frequency.setValueAtTime(f0, c.currentTime);
  if(f1 && f1 !== f0) osc.frequency.exponentialRampToValueAtTime(Math.max(20,f1), c.currentTime + dur);
  gain.gain.setValueAtTime(0, c.currentTime);
  gain.gain.linearRampToValueAtTime(peak || 0.14, c.currentTime + 0.008);
  gain.gain.exponentialRampToValueAtTime(0.0001, c.currentTime + dur);
  osc.connect(gain); gain.connect(c.destination);
  osc.start(); osc.stop(c.currentTime + dur + 0.02);
}
/* Short filtered noise burst — the "crack" half of a comic impact. */
function crack(dur, freq){
  var c = ac(); if(!c) return;
  var n = Math.max(1, Math.floor(c.sampleRate * dur));
  var buf = c.createBuffer(1, n, c.sampleRate);
  var d = buf.getChannelData(0);
  for(var i=0;i<n;i++) d[i] = (Math.random()*2-1) * Math.pow(1 - i/n, 2);
  var src = c.createBufferSource(); src.buffer = buf;
  var bp = c.createBiquadFilter(); bp.type = "bandpass"; bp.frequency.value = freq || 1400; bp.Q.value = 0.9;
  var gain = c.createGain(); gain.gain.setValueAtTime(0.22, c.currentTime);
  gain.gain.exponentialRampToValueAtTime(0.0001, c.currentTime + dur);
  src.connect(bp); bp.connect(gain); gain.connect(c.destination);
  src.start();
}

function sfxThwip(){ tone(1250, 320, 0.11, "sine", 0.09) }
function sfxBlip(){ tone(880, 880, 0.06, "triangle", 0.08) }
function sfxToggleOn(){ tone(520, 1040, 0.14, "sine", 0.1) }
function sfxToggleOff(){ tone(1040, 380, 0.12, "sine", 0.08) }
function sfxPow(){
  tone(160, 55, 0.22, "square", 0.16);
  crack(0.12, 1800);
}

/* ---------- comic burst text near a click point ---------- */
var BURST_WORDS = ["POW!","THWIP!","ZOOM!","BAM!","ZAP!"];
function spawnBurst(x, y){
  var el = document.createElement("div");
  el.className = "fx-burst";
  el.textContent = BURST_WORDS[Math.floor(Math.random()*BURST_WORDS.length)];
  el.style.left = x + "px"; el.style.top = y + "px";
  document.body.appendChild(el);
  setTimeout(function(){ el.remove() }, 620);
}

/* ---------- interaction wiring ---------- */
var POW_IDS = { searchBtn:1, aSave:1, calDetect:1, saveHunt:1, exp:1 };
document.addEventListener("click", function(e){
  if(!isOn()) return;
  resumeIfNeeded();
  var t = e.target;

  var navBtn = t.closest && t.closest('nav button[role="tab"]');
  if(navBtn){ sfxThwip(); return }

  var chip = t.closest && t.closest(".chip");
  if(chip && !chip.classList.contains("x")){ sfxThwip(); return }

  var pow = t.closest && t.closest("button, .qgo, a.qgo");
  if(pow && POW_IDS[pow.id]){
    sfxPow();
    spawnBurst(e.clientX || (pow.getBoundingClientRect().left+20), e.clientY || (pow.getBoundingClientRect().top));
    return;
  }
  var anyBtn = t.closest && t.closest("button");
  if(anyBtn && anyBtn.id !== "fxSwitch") sfxBlip();
}, true);

var sw = $("fxSwitch");
if(sw){
  sw.onclick = function(){
    var next = sw.getAttribute("aria-pressed") !== "true";
    setOn(next);
    resumeIfNeeded();
    if(next) sfxToggleOn(); else sfxToggleOff();
  };
}

/* ---------- boot ---------- */
document.body.classList.toggle("fx-on", isOn());
if(sw) sw.setAttribute("aria-pressed", isOn() ? "true" : "false");
})();
