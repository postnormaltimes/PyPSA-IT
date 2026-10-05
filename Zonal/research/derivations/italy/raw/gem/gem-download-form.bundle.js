function parsePrefillPayload(raw){if(typeof raw!="string"||!raw)return null;try{return JSON.parse(raw)}catch{return null}}function isPrefillFresh(payload,nowSeconds=Math.floor(Date.now()/1e3)){if(!payload||typeof payload!="object")return!1;let exp=Number(payload.exp);return Number.isFinite(exp)?exp>nowSeconds:!1}var CRC_TABLE=(()=>{let t=new Uint32Array(256);for(let n=0;n<256;n++){let c=n;for(let k=0;k<8;k++)c=c&1?3988292384^c>>>1:c>>>1;t[n]=c>>>0}return t})();function crc32(bytes){let c=4294967295;for(let i=0;i<bytes.length;i++)c=CRC_TABLE[(c^bytes[i])&255]^c>>>8;return(c^4294967295)>>>0}async function bundleAndDownload(entries,{zipName="download.zip"}={}){if(!entries?.length)throw new Error("no entries");let responses=await Promise.all(entries.map(async e=>{let r=await fetch(e.url,{credentials:"omit"});if(!r.ok)throw new Error(`fetch failed (${r.status}) for ${e.filename}`);let bytes=new Uint8Array(await r.arrayBuffer());return{filename:e.filename,bytes}})),zipBytes=encodeStoreZip(responses);triggerDownload(new Blob([zipBytes],{type:"application/zip"}),zipName)}async function bundleBlobsAndSave(entries,{zipName="download.zip"}={}){if(!entries?.length)throw new Error("no entries");let encoded=await Promise.all(entries.map(async e=>{let bytes=e.bytes instanceof Uint8Array?e.bytes:new Uint8Array(await e.blob.arrayBuffer());return{filename:e.filename,bytes}})),zipBytes=encodeStoreZip(encoded);triggerDownload(new Blob([zipBytes],{type:"application/zip"}),zipName)}function encodeStoreZip(entries){let encoded=entries.map(e=>{let nameBytes=new TextEncoder().encode(e.filename),crc=crc32(e.bytes);return{nameBytes,bytes:e.bytes,crc}}),localParts=[],centralParts=[],offset=0;for(let e of encoded){let localHeader=buildLocalFileHeader(e.nameBytes,e.crc,e.bytes.length);localParts.push(localHeader,e.bytes),centralParts.push(buildCentralDirEntry(e.nameBytes,e.crc,e.bytes.length,offset)),offset+=localHeader.length+e.bytes.length}let centralOffset=offset,centralSize=0;for(let c of centralParts)centralSize+=c.length;let end=buildEndOfCentralDir(encoded.length,centralSize,centralOffset);return concat([...localParts,...centralParts,end])}function buildLocalFileHeader(nameBytes,crc,size){let buf=new Uint8Array(30+nameBytes.length),v=new DataView(buf.buffer);return v.setUint32(0,67324752,!0),v.setUint16(4,20,!0),v.setUint16(6,2048,!0),v.setUint16(8,0,!0),v.setUint16(10,0,!0),v.setUint16(12,0,!0),v.setUint32(14,crc,!0),v.setUint32(18,size,!0),v.setUint32(22,size,!0),v.setUint16(26,nameBytes.length,!0),v.setUint16(28,0,!0),buf.set(nameBytes,30),buf}function buildCentralDirEntry(nameBytes,crc,size,localOffset){let buf=new Uint8Array(46+nameBytes.length),v=new DataView(buf.buffer);return v.setUint32(0,33639248,!0),v.setUint16(4,20,!0),v.setUint16(6,20,!0),v.setUint16(8,2048,!0),v.setUint16(10,0,!0),v.setUint16(12,0,!0),v.setUint16(14,0,!0),v.setUint32(16,crc,!0),v.setUint32(20,size,!0),v.setUint32(24,size,!0),v.setUint16(28,nameBytes.length,!0),v.setUint16(30,0,!0),v.setUint16(32,0,!0),v.setUint16(34,0,!0),v.setUint16(36,0,!0),v.setUint32(38,0,!0),v.setUint32(42,localOffset,!0),buf.set(nameBytes,46),buf}function buildEndOfCentralDir(entryCount,centralSize,centralOffset){let buf=new Uint8Array(22),v=new DataView(buf.buffer);return v.setUint32(0,101010256,!0),v.setUint16(4,0,!0),v.setUint16(6,0,!0),v.setUint16(8,entryCount,!0),v.setUint16(10,entryCount,!0),v.setUint32(12,centralSize,!0),v.setUint32(16,centralOffset,!0),v.setUint16(20,0,!0),buf}function concat(parts){let total=0;for(let p of parts)total+=p.length;let out=new Uint8Array(total),o=0;for(let p of parts)out.set(p,o),o+=p.length;return out}function triggerDownload(blob,filename){let url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url,a.download=filename,document.body.appendChild(a),a.click(),a.remove(),setTimeout(()=>URL.revokeObjectURL(url),1e3)}var PREFILL_FIELDS=["name","email","organization","sector","country"],USE_CASE_MIN_CHARS=100,MODES=Object.freeze({SLUGS:"slugs",DYNAMIC:"dynamic",NONE:"none"}),SECTORS=["Academic / Research","Civil Society / NGO","Financial / Investor","Government","Industry","Journalism / Media","Other"],COUNTRIES=["Afghanistan","Albania","Algeria","Andorra","Angola","Argentina","Armenia","Australia","Austria","Azerbaijan","Bahrain","Bangladesh","Belarus","Belgium","Benin","Bolivia","Bosnia and Herzegovina","Botswana","Brazil","Bulgaria","Cambodia","Cameroon","Canada","Chad","Chile","China","Colombia","Costa Rica","Croatia","Cuba","Czechia","Denmark","Dominican Republic","Ecuador","Egypt","El Salvador","Estonia","Ethiopia","Finland","France","Georgia","Germany","Ghana","Greece","Guatemala","Honduras","Hungary","Iceland","India","Indonesia","Iran","Iraq","Ireland","Israel","Italy","Jamaica","Japan","Jordan","Kazakhstan","Kenya","Kuwait","Kyrgyzstan","Laos","Latvia","Lebanon","Libya","Lithuania","Luxembourg","Madagascar","Malaysia","Mali","Malta","Mexico","Moldova","Mongolia","Montenegro","Morocco","Mozambique","Myanmar","Namibia","Nepal","Netherlands","New Zealand","Nicaragua","Niger","Nigeria","North Korea","North Macedonia","Norway","Oman","Pakistan","Panama","Paraguay","Peru","Philippines","Poland","Portugal","Qatar","Romania","Russia","Rwanda","Saudi Arabia","Senegal","Serbia","Singapore","Slovakia","Slovenia","Somalia","South Africa","South Korea","Spain","Sri Lanka","Sudan","Sweden","Switzerland","Syria","Taiwan","Tajikistan","Tanzania","Thailand","Tunisia","Turkey","Turkmenistan","Uganda","Ukraine","United Arab Emirates","United Kingdom","United States","Uruguay","Uzbekistan","Venezuela","Vietnam","Yemen","Zambia","Zimbabwe"],STYLES=`
  :host {
    --gem-ink: #0f4a5c;
    --gem-ink-strong: #0b3a49;
    --gem-accent: #e85d3c;
    --gem-accent-hover: #d14d2d;
    --gem-surface: #f1ede2;
    --gem-card: #ffffff;
    --gem-border: #0f4a5c;
    --gem-muted: #8fa3aa;
    --gem-chip: #ebe6d6;
    --gem-radius: 10px;
    --gem-font: -apple-system, BlinkMacSystemFont, "Segoe UI", "Inter", system-ui, sans-serif;

    display: block;
    /* Without an explicit width, the host collapses to 0px when its parent
       uses display:flex (e.g. Drupal's paragraph--type--embed wrapper),
       which shrinks all the form content to per-word wrapping in a tiny
       column. Default to filling the container; the .card inside still
       constrains to max-width: 640px. */
    width: 100%;
    color: var(--gem-ink);
    font-family: var(--gem-font);
    font-size: 16px;
    line-height: 1.4;
    box-sizing: border-box;
    container-type: inline-size;
  }
  *, *::before, *::after { box-sizing: border-box; }

  dialog {
    padding: 0;
    border: none;
    color: inherit;
    margin: 0;
    background: transparent;
    max-width: none;
    max-height: none;
  }
  dialog[open] {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 100vw;
    height: 100vh;
    padding: 16px;
  }
  dialog::backdrop {
    background: rgba(11, 58, 73, 0.55);
    backdrop-filter: blur(2px);
  }
  dialog .card { max-height: 100%; overflow-y: auto; width: 100%; max-width: 640px; }

  .card {
    background: var(--gem-surface);
    border-radius: 14px;
    padding: 28px 30px 32px;
    /* Default max-width matches the original narrow-modal sizing. Override
       via gem-download-form { --gem-card-max-width: 800px } per-page when
       the form needs more breathing room (e.g. the Subscribe page). The
       wide-layout @container rule below sets max-width: none for the
       2-col aside layout. */
    max-width: var(--gem-card-max-width, 640px);
    /* Center the card within the host. Without this, in wide host
       containers (e.g. the Drupal embed-tabs-wrapper at 1100+px) the
       card hugs the left edge and leaves dead space to the right. */
    margin-left: auto;
    margin-right: auto;
    position: relative;
  }

  .header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
    margin-bottom: 6px;
  }

  h2.title {
    font-size: 30px;
    font-weight: 800;
    margin: 0;
    color: var(--gem-ink-strong);
    letter-spacing: -0.01em;
  }

  .subtitle {
    margin: 6px 0 22px;
    font-size: 18px;
    color: var(--gem-ink);
  }
  .subtitle:empty { display: none; }

  /* Integrator slot for slug-selection UI (checkboxes, <select>, etc).
     The <slot> itself has display:contents by default; integrator owns all
     child styling. Margin added so the picker visually separates from the
     identity fields below. */
  ::slotted(*) { margin-bottom: 18px; }

  .close-btn {
    font: inherit;
    background: var(--gem-chip);
    border: none;
    color: var(--gem-ink-strong);
    font-weight: 700;
    padding: 8px 14px;
    border-radius: 8px;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 8px;
    flex-shrink: 0;
  }
  .close-btn:hover { background: #dfd9c5; }
  .close-btn svg { width: 14px; height: 14px; }

  form { display: flex; flex-direction: column; gap: 18px; }

  .field { display: flex; flex-direction: column; gap: 6px; }

  label {
    font-weight: 700;
    font-size: 16px;
    color: var(--gem-ink-strong);
  }
  label .req { color: var(--gem-ink-strong); }

  input[type="text"],
  input[type="email"],
  select,
  textarea {
    font: inherit;
    color: var(--gem-ink-strong);
    background: var(--gem-card);
    border: 1.5px solid var(--gem-border);
    border-radius: var(--gem-radius);
    padding: 14px 16px;
    width: 100%;
    outline: none;
    transition: border-color .15s, box-shadow .15s;
  }
  input::placeholder, textarea::placeholder { color: #9aa7ad; }

  input:focus, select:focus, textarea:focus {
    border-color: var(--gem-accent);
    box-shadow: 0 0 0 3px rgba(232, 93, 60, 0.15);
  }

  select {
    appearance: none;
    -webkit-appearance: none;
    background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='%230f4a5c' stroke-width='3' stroke-linecap='round' stroke-linejoin='round'><polyline points='6 9 12 15 18 9'/></svg>");
    background-repeat: no-repeat;
    background-position: right 16px center;
    padding-right: 44px;
  }
  select:invalid, select option[value=""] { color: #9aa7ad; }

  textarea { min-height: 140px; resize: vertical; font-family: inherit; }

  .use-counter {
    font-size: 13px;
    color: var(--gem-muted);
    margin-top: -2px;
  }
  .use-counter.ok { color: var(--gem-ink); }

  .license-note { font-size: 15px; margin: 6px 0 -4px; color: var(--gem-ink); }
  .license-note a {
    color: var(--gem-ink-strong);
    font-weight: 700;
    text-decoration: underline;
    text-underline-offset: 3px;
  }

  .check {
    display: flex;
    align-items: flex-start;
    gap: 14px;
    background: var(--gem-chip);
    border-radius: var(--gem-radius);
    padding: 14px 16px;
    cursor: pointer;
    user-select: none;
  }
  .check input {
    appearance: none;
    -webkit-appearance: none;
    width: 22px;
    height: 22px;
    margin: 1px 0 0;
    border: 1.5px solid var(--gem-border);
    border-radius: 4px;
    background: var(--gem-card);
    flex-shrink: 0;
    cursor: pointer;
    display: grid;
    place-content: center;
    transition: background .15s, border-color .15s;
  }
  .check input:checked {
    background: var(--gem-ink-strong);
    border-color: var(--gem-ink-strong);
  }
  .check input:checked::after {
    content: "";
    width: 12px;
    height: 7px;
    border-left: 2px solid #fff;
    border-bottom: 2px solid #fff;
    transform: rotate(-45deg) translate(1px, -1px);
  }
  .check input:focus-visible { outline: 2px solid var(--gem-accent); outline-offset: 2px; }
  .check span { font-weight: 700; color: var(--gem-ink-strong); font-size: 16px; }

  button.submit {
    font: inherit;
    font-weight: 800;
    font-size: 18px;
    background: var(--gem-accent);
    color: #fff;
    border: none;
    border-radius: var(--gem-radius);
    padding: 16px 28px;
    cursor: pointer;
    align-self: flex-start;
    margin-top: 10px;
    letter-spacing: 0.01em;
    transition: background .15s, transform .05s;
  }
  button.submit:hover { background: var(--gem-accent-hover); }
  button.submit:active { transform: translateY(1px); }
  button.submit:disabled { opacity: .6; cursor: not-allowed; }

  .error {
    color: #b23a1a;
    font-size: 14px;
    font-weight: 600;
    margin-top: 2px;
    display: none;
  }

  .ready-panel {
    background: var(--gem-chip);
    border-radius: var(--gem-radius);
    padding: 14px 16px;
    margin-top: 10px;
    font-size: 15px;
  }
  /* Thank-you confirmation \u2014 full-page modal dialog. Same dialog reset
     overrides as the main modal so styling doesn't leak from UA defaults. */
  dialog.thank-you {
    padding: 0;
    border: none;
    color: inherit;
    margin: 0;
    background: transparent;
    max-width: none;
    max-height: none;
  }
  dialog.thank-you[open] {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 100vw;
    height: 100vh;
    padding: 16px;
  }
  dialog.thank-you::backdrop {
    background: rgba(11, 58, 73, 0.55);
    backdrop-filter: blur(2px);
  }
  .thank-you-card {
    background: var(--gem-surface);
    border-radius: 14px;
    padding: 28px 30px 32px;
    width: 100%;
    max-width: 520px;
    position: relative;
    color: var(--gem-ink);
  }
  .thank-you-card .close-btn {
    position: absolute;
    top: 16px;
    right: 18px;
    font: inherit;
    background: var(--gem-chip);
    border: none;
    color: var(--gem-ink-strong);
    font-weight: 700;
    padding: 8px 14px;
    border-radius: 8px;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 8px;
  }
  .thank-you-card .close-btn:hover { background: #dfd9c5; }
  .thank-you-card .close-btn svg { width: 14px; height: 14px; }
  .thank-you-card h3 {
    margin: 0 0 10px;
    font-size: 26px;
    font-weight: 800;
    color: var(--gem-ink-strong);
    padding-right: 90px;
  }
  .thank-you-card p { margin: 0; font-size: 17px; line-height: 1.5; }
  .ready-panel ul.ready-links {
    margin: 8px 0 0;
    padding-left: 22px;
  }
  .ready-panel ul.ready-links li { margin: 4px 0; }
  .ready-panel .error-note {
    margin: 8px 0 0;
    font-weight: 600;
  }
  .ready-panel a,
  .ready-panel button.inline-link {
    color: var(--gem-accent);
    font-weight: 800;
    text-decoration: underline;
    text-underline-offset: 3px;
    background: none;
    border: none;
    padding: 0;
    font: inherit;
    cursor: pointer;
  }
  .ready-panel a:hover,
  .ready-panel button.inline-link:hover { color: var(--gem-accent-hover); }
  .field.invalid .error { display: block; }
  .field.invalid input,
  .field.invalid select,
  .field.invalid textarea {
    border-color: #b23a1a;
  }
  .check.invalid { outline: 1.5px solid #b23a1a; }

  /* Wide layout \u2014 only triggers when the host container is roomy AND the
     integrator opted in by slotting [slot="aside"] content. Browsers
     without container-query or :has() support ignore this entirely and
     fall back to the default 1-col stack. */
  @container (min-width: 820px) {
    :host([has-aside]) .card {
      max-width: none;
      padding: 40px 48px 48px;
      background: rgb(45, 106, 129);
      color: #fff;
      border-radius: 18px;
    }
    :host([has-aside]) h2.title {
      text-align: center;
      margin-bottom: 32px;
      color: #fff;
    }
    :host([has-aside]) .header {
      display: block;
      position: relative;
      margin-bottom: 0;
    }
    :host([has-aside]) .header .close-btn {
      position: absolute;
      top: 0;
      right: 0;
    }
    :host([has-aside]) .card-body {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      gap: 40px;
      align-items: start;
    }
    :host([has-aside]) .aside-pane,
    :host([has-aside]) .aside-pane .subtitle { color: #fff; }
    /* !important because light-DOM author styles on the slotted element
       always win the cascade over shadow ::slotted() at equal specificity.
       The color we want is determined by the dark card we're rendering on,
       not by the integrator's page palette. */
    :host([has-aside]) ::slotted([slot="aside"]) { color: #fff !important; }
    :host([has-aside]) .form-pane {
      background: var(--gem-surface);
      /* Top-left squared so the cream pane reads as flowing out of the
         dark card edge; other corners rounded. */
      border-radius: 0 18px 18px 18px;
      padding: 32px 36px 36px;
      color: var(--gem-ink);
    }
  }

  @media (max-width: 520px) {
    .card { padding: 22px 20px 26px; border-radius: 10px; }
    h2.title { font-size: 24px; }
    .subtitle { font-size: 16px; }
    button.submit { width: 100%; }
  }
`,GemDownloadForm=class extends HTMLElement{static get observedAttributes(){return["title","subtitle","submit-label","license-url","modal","open","storage-key","prefill-ttl-seconds","submissions-url","supabase-key","presign-url","slugs","download-endpoint","download-params","auto-download","form-key","thank-you-message","thank-you-title","email-label","show-errors","hide-errors"]}constructor(){super(),this.attachShadow({mode:"open"})}connectedCallback(){this._autoSlotAside(),this.render(),this.hasAttribute("open")&&this.open(),this._isModal||this._emitFormOpen(),this.addEventListener("gem-download-complete",e=>{let d=e.detail||{};d.ok&&this.shadowRoot.querySelector(".ready-panel")?.remove(),this._emitGemEvent("export:complete",{source:"download-form",ok:!!d.ok,kind:d.kind||"",error:d.ok?"":d.error||"unknown"})})}_autoSlotAside(){for(let child of this.children)child.tagName==="SUBTITLE"&&!child.hasAttribute("slot")&&child.setAttribute("slot","aside")}attributeChangedCallback(name,oldVal,newVal){if(this.shadowRoot.firstChild)switch(name){case"open":{let isOpen=newVal!==null,dlg=this.shadowRoot.querySelector("dialog");dlg&&isOpen&&!dlg.open?dlg.showModal():dlg&&!isOpen&&dlg.open&&dlg.close();return}case"storage-key":return this._syncStorageKey();case"title":{let t=this.shadowRoot.querySelector("h2.title");t&&(t.textContent=this._title);return}case"subtitle":{let p=this.shadowRoot.querySelector("p.subtitle");p&&(p.textContent=this._subtitle);return}case"submit-label":{let b=this.shadowRoot.querySelector("button.submit");b&&(b.textContent=this._submitLabel);return}case"license-url":{let a=this.shadowRoot.querySelector(".license-note a");a&&(a.href=this._licenseUrl);return}case"modal":return this._syncModalWrapper()}}get _title(){return this.getAttribute("title")||"Download data"}get _subtitle(){let attr=this.getAttribute("subtitle");return attr!==null?attr:"Fill out the form to receive your selected datasets in a ZIP file."}get _submitLabel(){return this.getAttribute("submit-label")||"Submit and Download"}get _emailOptinLabel(){let attr=this.getAttribute("email-optin-label");return attr===null?"Send me email updates on projects associated with my download choices":attr===""?null:attr}get _emailLabel(){let attr=this.getAttribute("email-label");return attr===null?"Email Address":attr===""?null:attr}get _rememberMeLabel(){let attr=this.getAttribute("remember-me-label");return attr===null?"Remember me on this device (saves name, email, organization, sector, country)":attr===""?null:attr}get _showClose(){return this._isModal}get _isModal(){return this.hasAttribute("modal")}get _licenseUrl(){return this.getAttribute("license-url")||"/creative-commons-license"}get _storageKey(){return this.getAttribute("storage-key")||""}get _prefillStorage(){let raw=this.getAttribute("prefill-ttl-seconds");if(raw===null)return{type:"session",ttl:null};let n=Number(raw);return!Number.isFinite(n)||n<=0?{type:"session",ttl:null}:{type:"local",ttl:Math.floor(n)}}get _submissionsUrl(){return this.getAttribute("submissions-url")||"https://auxunjnrktkmeqyoyngm.supabase.co/rest/v1/rpc/mint_submission"}get _supabaseKey(){return this.getAttribute("supabase-key")||"sb_publishable_8mQAV8B2HhveNc5T8VGqPQ_1lgsFAvz"}get _presignUrl(){return this.getAttribute("presign-url")||"https://auxunjnrktkmeqyoyngm.supabase.co/functions/v1/presign"}get _autoDownload(){return this.getAttribute("auto-download")!=="false"}get _slugs(){let raw=[],slotted=[...this.querySelectorAll('[name="slug"], [name="slugs"]')];for(let el of slotted)if(el.tagName==="SELECT")for(let opt of el.selectedOptions)raw.push(opt.value);else el.type==="checkbox"||el.type==="radio"?el.checked&&raw.push(el.value):el.value!=null&&raw.push(el.value);let attr=this.getAttribute("slugs");attr&&raw.push(attr);let flat=raw.flatMap(v=>String(v).split(",")).map(s=>s.trim()).filter(Boolean),seen=new Set;return flat.filter(s=>!seen.has(s)&&seen.add(s))}get _downloadEndpoint(){return this.getAttribute("download-endpoint")||""}get _downloadParams(){return this.getAttribute("download-params")||""}get _downloadParamsList(){let out=[],slotted=[...this.querySelectorAll('[name="download-params"]')];for(let el of slotted){let v=el.value;v!=null&&v!==""&&out.push(String(v))}let attr=this.getAttribute("download-params");return attr&&out.push(attr),out}get _isMultiParamsDynamic(){return this._downloadEndpoint?this._downloadParamsList.length>1:!1}get _hasDownloadSurface(){return!!(this.getAttribute("slugs")||this.getAttribute("download-endpoint")||this.querySelector('[name="slug"], [name="slugs"]')||this.querySelector('[name="download-params"]'))}get _requestMode(){return this._slugs.length>0?MODES.SLUGS:this._downloadEndpoint?MODES.DYNAMIC:MODES.NONE}get _formKey(){return this.getAttribute("form-key")||""}get _thankYouMessage(){let a=this.getAttribute("thank-you-message");return a===null?null:a||"Your submission has been received."}get _thankYouTitle(){return this.getAttribute("thank-you-title")||"Thanks!"}get _isExtendedForm(){return!!this._formKey&&!this._hasDownloadSurface}_harvestCustomFields(){let out={},assign=(key,value)=>{key in out?out[key]=Array.isArray(out[key])?[...out[key],value]:[out[key],value]:out[key]=value},controls=[...this.querySelectorAll("[name]")].filter(el=>el.name!=="slug"&&el.name!=="slugs"&&el.name!=="email"&&el.name!=="download-params"),seenRadios=new Set;for(let el of controls){if(el.type==="radio"){if(seenRadios.has(el.name))continue;let checked=this.querySelector(`input[type="radio"][name="${cssEscape(el.name)}"]:checked`);if(seenRadios.add(el.name),!checked)continue;assign(el.name,checked.value);continue}if(el.type==="checkbox"){if(!el.checked)continue;assign(el.name,el.value||"on");continue}if(el.tagName==="SELECT"&&el.multiple){assign(el.name,[...el.selectedOptions].map(o=>o.value));continue}el.value==null||el.value===""||assign(el.name,el.value)}return out}open(){if(!this._isModal)return;let dlg=this.shadowRoot.querySelector("dialog");dlg&&!dlg.open&&dlg.showModal(),this.hasAttribute("open")||this.setAttribute("open",""),this._emitFormOpen()}close(){let dlg=this.shadowRoot.querySelector("dialog");dlg&&dlg.open?dlg.close():this.dispatchEvent(new CustomEvent("gem-close",{bubbles:!0,composed:!0})),this.hasAttribute("open")&&this.removeAttribute("open")}render(){let sectorOptions=SECTORS.map(s=>`<option value="${s}">${s}</option>`).join(""),countryOptions=COUNTRIES.map(c=>`<option value="${c}">${c}</option>`).join(""),cardMarkup=`
      <div class="card" part="card">
        <div class="header">
          <h2 class="title">${escapeHtml(this._title)}</h2>
          ${this._showClose?`
            <button type="button" class="close-btn" data-close aria-label="Close">
              Close
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
            </button>`:""}
        </div>
        <div class="card-body">
          <div class="aside-pane">
            <p class="subtitle">${escapeHtml(this._subtitle)}</p>
            <slot name="aside"></slot>
          </div>
          <div class="form-pane">
            <slot></slot>

            <form novalidate>
          ${this._isExtendedForm?"":`
          <div class="field">
            <label for="name">Full Name<span class="req">*</span></label>
            <input id="name" name="name" type="text" autocomplete="name" required />
            <div class="error">Please enter your name.</div>
          </div>`}

          ${this._emailLabel?`
          <div class="field">
            <label for="email">${escapeHtml(this._emailLabel)}<span class="req">*</span></label>
            <input id="email" name="email" type="email" autocomplete="email" required />
            <div class="error">Please enter a valid email address.</div>
          </div>`:""}

          ${this._isExtendedForm?"":`
          <div class="field">
            <label for="organization">Organization<span class="req">*</span></label>
            <input id="organization" name="organization" type="text" autocomplete="organization" required />
            <div class="error">Please enter your organization.</div>
          </div>

          <div class="field">
            <label for="sector">Sector<span class="req">*</span></label>
            <select id="sector" name="sector" required>
              <option value="" disabled selected>Select one...</option>
              ${sectorOptions}
            </select>
            <div class="error">Please select a sector.</div>
          </div>

          <div class="field">
            <label for="country">Country</label>
            <select id="country" name="country">
              <option value="" disabled selected>Select one...</option>
              ${countryOptions}
            </select>
          </div>

          <div class="field">
            <label for="use">How do you plan to use the data? (Please be specific.)<span class="req">*</span></label>
            <textarea id="use" name="use" placeholder="Example Text" required minlength="${USE_CASE_MIN_CHARS}"></textarea>
            <div class="use-counter" aria-live="polite">0 of ${USE_CASE_MIN_CHARS} minimum characters</div>
            <div class="error">Please describe how you plan to use the data (${USE_CASE_MIN_CHARS}+ characters).</div>
          </div>`}

          ${this._hasDownloadSurface?`
          <p class="license-note">
            Please review Global Energy Monitor's
            <a href="${escapeAttr(this._licenseUrl)}" target="_blank" rel="noopener noreferrer">Creative Commons Public License</a>
          </p>

          <label class="check" data-check="license">
            <input type="checkbox" name="license" required />
            <span>I have reviewed and understand Global Energy Monitor's Creative Commons Public License<span class="req">*</span></span>
          </label>`:""}

          ${this._storageKey&&this._rememberMeLabel?`
            <label class="check" data-check="remember_me">
              <input type="checkbox" name="remember_me" />
              <span>${escapeHtml(this._rememberMeLabel)}</span>
            </label>`:""}

          ${this._emailOptinLabel?`
            <label class="check" data-check="email_optin">
              <input type="checkbox" name="email_optin" />
              <span>${escapeHtml(this._emailOptinLabel)}</span>
            </label>`:""}

          <!-- Extra integrator-supplied checkboxes / fields, placed below the
               builtins so they're visually grouped with license + optin. Each
               slotted control's [name] is harvested into custom_fields by
               _harvestCustomFields, same as any other slotted control. Pair
               with class="gem-check" from gem-download-form.css for the cream
               rounded-box styling that matches the builtins. -->
          <slot name="extras"></slot>

          <button type="submit" class="submit">${escapeHtml(this._submitLabel)}</button>
        </form>
          </div>
        </div>
      </div>
    `;this.shadowRoot.innerHTML=`
      <style>${STYLES}</style>
      ${this._isModal?`<dialog part="dialog">${cardMarkup}</dialog>`:cardMarkup}
    `,this._bind(),this._loadPrefill()}_bind(){let form=this.shadowRoot.querySelector("form"),closeBtn=this.shadowRoot.querySelector("[data-close]"),dlg=this.shadowRoot.querySelector("dialog");if(closeBtn&&closeBtn.addEventListener("click",()=>this.close()),dlg){dlg.addEventListener("close",()=>{this.hasAttribute("open")&&this.removeAttribute("open"),this.dispatchEvent(new CustomEvent("gem-close",{bubbles:!0,composed:!0}))}),dlg.addEventListener("click",e=>{e.target===dlg&&this.close()});let stopKeys=e=>e.stopPropagation();dlg.addEventListener("keydown",stopKeys),dlg.addEventListener("keyup",stopKeys),dlg.addEventListener("keypress",stopKeys)}form.addEventListener("submit",e=>{e.preventDefault(),this._handleSubmit(form)});let clearHandler=e=>{e.target.matches("input, select, textarea")&&this._clearFieldError(e.target)};form.addEventListener("input",clearHandler),form.addEventListener("change",clearHandler);let asideSlot=this.shadowRoot.querySelector('slot[name="aside"]');if(asideSlot){let syncAside=()=>{let present=asideSlot.assignedNodes({flatten:!0}).some(n=>n.nodeType!==Node.TEXT_NODE||n.textContent.trim());this.toggleAttribute("has-aside",present)};asideSlot.addEventListener("slotchange",syncAside),syncAside()}let useArea=form.querySelector('textarea[name="use"]'),counter=form.querySelector(".use-counter");if(useArea&&counter){let updateCounter=()=>{let len=useArea.value.trim().length;counter.textContent=`${len} of ${USE_CASE_MIN_CHARS} minimum characters`,counter.classList.toggle("ok",len>=USE_CASE_MIN_CHARS)};useArea.addEventListener("input",updateCounter),updateCounter()}}_collect(form){let fd=new FormData(form),obj={};for(let[k,v]of fd.entries())obj[k]=v;if(!obj.email){let slotted=this.querySelector('[name="email"]');slotted&&(obj.email=slotted.value||"")}let eo=form.querySelector('input[name="email_optin"]');obj.email_optin=!!(eo&&eo.checked);let lic=form.querySelector('input[name="license"]');obj.license=!!(lic&&lic.checked);let rm=form.querySelector('input[name="remember_me"]');return obj.remember_me=!!(rm&&rm.checked),obj}_validate(form){let ok=!0;form.querySelectorAll(".field").forEach(f=>f.classList.remove("invalid")),form.querySelectorAll(".check").forEach(c=>c.classList.remove("invalid"));let required=this._isExtendedForm?[["email",v=>/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v)]]:[["name",v=>v.trim().length>0],["email",v=>/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v)],["organization",v=>v.trim().length>0],["sector",v=>v.trim().length>0],["use",v=>v.trim().length>=USE_CASE_MIN_CHARS]];for(let[name,test]of required){let el=form.querySelector(`[name="${name}"]`);el&&(test(el.value)||(el.closest(".field").classList.add("invalid"),ok=!1))}if(this._isExtendedForm){let slotted=[...this.querySelectorAll("[required]")];for(let el of slotted)if(typeof el.checkValidity=="function"&&!el.checkValidity()){typeof el.reportValidity=="function"&&el.reportValidity(),ok=!1;break}}let license=form.querySelector('input[name="license"]');return license&&!license.checked&&(license.closest(".check").classList.add("invalid"),ok=!1),ok||form.querySelector(".field.invalid, .check.invalid")?.scrollIntoView({behavior:"smooth",block:"center"}),ok}_clearFieldError(el){el.closest(".field")?.classList.remove("invalid"),el.closest(".check")?.classList.remove("invalid")}_loadPrefill(){let key=this._storageKey;if(!key)return;let{type}=this._prefillStorage,store=type==="local"?localStorage:sessionStorage,raw;try{raw=store.getItem(key)}catch{return}if(!raw)return;let payload=parsePrefillPayload(raw);if(!payload)return;if(type==="local"&&!isPrefillFresh(payload)){try{store.removeItem(key)}catch{}return}let form=this.shadowRoot.querySelector("form");if(!form)return;for(let name of PREFILL_FIELDS){let el=form.querySelector(`[name="${name}"]`);el&&payload[name]!=null&&payload[name]!==""&&(el.value=payload[name])}let rm=form.querySelector('input[name="remember_me"]');rm&&(rm.checked=!0)}_savePrefill(data){let key=this._storageKey;if(!key||this._rememberMeLabel===null)return;let{type,ttl}=this._prefillStorage,store=type==="local"?localStorage:sessionStorage;try{if(data.remember_me){let payload={};for(let f of PREFILL_FIELDS)payload[f]=data[f]||"";if(type==="local"){let nowSec=Math.floor(Date.now()/1e3);payload.iat=nowSec,payload.exp=nowSec+ttl}store.setItem(key,JSON.stringify(payload))}else{try{localStorage.removeItem(key)}catch{}try{sessionStorage.removeItem(key)}catch{}}}catch{}}async _handleSubmit(form){if(!this._validate(form))return;let data=this._collect(form),pre=new CustomEvent("gem-submit",{bubbles:!0,composed:!0,cancelable:!0,detail:{data,requestMode:this._requestMode}});if(this.dispatchEvent(pre),pre.defaultPrevented)return;let url=this._submissionsUrl;if(!url){this.dispatchEvent(new CustomEvent("gem-noop",{bubbles:!0,composed:!0,detail:{reason:"no-submissions-url",data}}));return}if(this._isMultiParamsDynamic){this._setSubmitting(!0);try{await this._handleMultiParamsSubmit(data,url)}finally{this._setSubmitting(!1)}return}this._setSubmitting(!0);try{let payload=this._buildSubmissionPayload(data),headers={"content-type":"application/json"},key=this._supabaseKey;key&&(headers.apikey=key,headers.authorization=`Bearer ${key}`);let res=await fetch(url,{method:"POST",headers,body:JSON.stringify(payload)});if(!res.ok){let text=await res.text().catch(()=>"");throw new Error(`submissions api ${res.status}: ${text.slice(0,200)}`)}let response=await res.json();this._savePrefill(data),this.dispatchEvent(new CustomEvent("gem-submit-success",{bubbles:!0,composed:!0,detail:{data,response}})),this._dispatchDownload(response,data),response?.mode===MODES.NONE&&this._thankYouMessage!==null&&this._showThankYou()}catch(err){this._showDownloadError("Sorry \u2014 something went wrong submitting the form. Please try again."),this.dispatchEvent(new CustomEvent("gem-submit-error",{bubbles:!0,composed:!0,detail:{data,error:err,message:String(err?.message??err)}}))}finally{this._setSubmitting(!1)}}async _handleMultiParamsSubmit(data,submissionsUrl){let paramsList=this._downloadParamsList,endpoint=this._downloadEndpoint,headers={"content-type":"application/json"},key=this._supabaseKey;key&&(headers.apikey=key,headers.authorization=`Bearer ${key}`);let firstResponse=null,results;try{results=await Promise.all(paramsList.map(async params=>{let payload=this._buildSubmissionPayload(data,params),mintRes=await fetch(submissionsUrl,{method:"POST",headers,body:JSON.stringify(payload)});if(!mintRes.ok){let text=await mintRes.text().catch(()=>"");throw new Error(`submissions api ${mintRes.status}: ${text.slice(0,200)}`)}let response=await mintRes.json();if(firstResponse||(firstResponse=response),!response?.capability_token)throw new Error("mint returned no capability_token for multi-params entry");return this._fetchDownloadBlob(endpoint,params,response.capability_token)}))}catch(err){this._showDownloadError("Sorry \u2014 something went wrong preparing your download. Please try again."),this.dispatchEvent(new CustomEvent("gem-submit-error",{bubbles:!0,composed:!0,detail:{data,error:err,message:String(err?.message??err),stage:"multi-mint-or-download"}}));return}this._savePrefill(data),this.dispatchEvent(new CustomEvent("gem-submit-success",{bubbles:!0,composed:!0,detail:{data,response:firstResponse,kind:"multi-dynamic",count:results.length}})),this._emitExportStart(data),this._setDownloading();let zipName=this.getAttribute("zip-name")||"gem-download.zip",ok=!1,errMsg=null;try{let used=new Set,entries=results.map(({blob,filename},i)=>{let name=filename||`download-${i+1}`;if(used.has(name)){let dot=name.lastIndexOf("."),base=dot>0?name.slice(0,dot):name,ext=dot>0?name.slice(dot):"",n=1;for(;used.has(`${base}-${n}${ext}`);)n++;name=`${base}-${n}${ext}`}return used.add(name),{filename:name,blob}});this._autoDownload&&await bundleBlobsAndSave(entries,{zipName}),ok=!0}catch(err){errMsg=String(err?.message??err),this._showDownloadError("Sorry \u2014 the download couldn\u2019t be assembled. Please try again."),this.dispatchEvent(new CustomEvent("gem-submit-error",{bubbles:!0,composed:!0,detail:{data,error:err,message:errMsg,stage:"multi-bundle"}}))}finally{this._setButtonState("idle"),this.dispatchEvent(new CustomEvent("gem-download-complete",{bubbles:!0,composed:!0,detail:{kind:"multi-dynamic",ok,error:errMsg,filename:ok?zipName:null,count:results?.length??0}}))}}async _fetchDownloadBlob(endpoint,params,token){let action=params?`${endpoint}?${params}`:endpoint,r=await fetch(action,{method:"POST",body:new URLSearchParams({token}),credentials:"omit"});if(!r.ok){let text=await r.text().catch(()=>"");throw new Error(`download ${r.status}: ${text.slice(0,200)}`)}let match=(r.headers.get("content-disposition")||"").match(/filename\*?=(?:UTF-8''|")?([^";]+)"?/i),filename=match?decodeURIComponent(match[1]):null;return{blob:await r.blob(),filename}}_buildSubmissionPayload(data,paramsOverride){let ext=this._isExtendedForm,base={name:ext?null:data.name,email:data.email,organization:ext?null:data.organization,sector:ext?null:data.sector,country:ext?null:data.country||"",use_case:ext?null:data.use,license_text:this._licenseText(),email_optin:!!data.email_optin,request_mode:this._requestMode,useragent:typeof navigator<"u"?navigator.userAgent:"",page_url:typeof window<"u"?window.location.href:""};if(this._formKey){base.form_key=this._formKey;let custom=this._harvestCustomFields();Object.keys(custom).length&&(base.custom_fields=custom)}return this._requestMode===MODES.SLUGS?base.requested_slugs=this._slugs:this._requestMode===MODES.DYNAMIC&&(base.dynamic_params=paramsOverride??this._downloadParams),base}_licenseText(){return this._hasDownloadSurface?this.getAttribute("license-text")||"Creative Commons Attribution 4.0 International (CC BY 4.0) \u2014 "+this._licenseUrl:null}_emitGemEvent(name,data){try{let detail={name,data,ts:Date.now()};this.dispatchEvent(new CustomEvent("gem:event",{detail,bubbles:!0,composed:!0}))}catch{}}_downloadTracker(){let slugs=this._slugs;if(slugs.length)return slugs.join(",");let types=[];for(let p of this._downloadParamsList)try{let at=new URLSearchParams(p).get("asset_type");at&&types.push(at)}catch{}return types.join(",")}_emitFormOpen(){this._emitGemEvent("form:open",{source:"download-form",modal:this._isModal,tracker:this._downloadTracker()})}_emitExportStart(data){this._emitGemEvent("export:start",{source:"download-form",tracker:this._downloadTracker(),sector:data.sector||"",country:data.country||"",organization:data.organization||""})}async _dispatchDownload(response,data){!response||!response.mode||(response.mode===MODES.SLUGS?(this._emitExportStart(data),await this._presignAndDownload(response)):response.mode===MODES.DYNAMIC&&response.capability_token&&(this._emitExportStart(data),this._dynamicDownload(response)))}async _presignAndDownload(response){let presignUrl=this._presignUrl;if(!presignUrl){this.dispatchEvent(new CustomEvent("gem-submit-error",{bubbles:!0,composed:!0,detail:{error:new Error("missing presign-url attribute"),message:"missing presign-url"}}));return}try{let r=await fetch(presignUrl,{method:"POST",headers:{"content-type":"application/json",authorization:`Bearer ${response.capability_token}`}});if(!r.ok)throw new Error(`presign ${r.status}: ${(await r.text()).slice(0,200)}`);let{urls}=await r.json();if(!Array.isArray(urls)||urls.length===0)throw new Error("presign returned no urls");if(urls.length===1){let{url,slug,filename}=urls[0];this._showSingleDownloadReady(url,slug),this.dispatchEvent(new CustomEvent("gem-download-ready",{bubbles:!0,composed:!0,detail:{kind:"single",url,slug,filename,urls,response,autoDownload:this._autoDownload}})),this._autoDownload&&await this._fetchAndSaveBlob(url,"single",filename||slug)}else this._showMultiDownloadReady(urls),this.dispatchEvent(new CustomEvent("gem-download-ready",{bubbles:!0,composed:!0,detail:{kind:"multi",urls,response,autoDownload:this._autoDownload}})),this._autoDownload&&await this._bundleAndSave(urls)}catch(err){this._showDownloadError("Sorry \u2014 something went wrong preparing your download. Please try again."),this.dispatchEvent(new CustomEvent("gem-submit-error",{bubbles:!0,composed:!0,detail:{error:err,message:String(err?.message??err),stage:"presign"}}))}}_showThankYou(){this.shadowRoot.querySelector("dialog.thank-you")?.remove();let dlg=document.createElement("dialog");dlg.className="thank-you",dlg.innerHTML=`
      <div class="thank-you-card" role="status" aria-live="polite">
        <button type="button" class="close-btn" data-thankyou-close aria-label="Close">
          Close
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
        </button>
        <h3>${escapeHtml(this._thankYouTitle)}</h3>
        <p>${escapeHtml(this._thankYouMessage)}</p>
      </div>
    `,this.shadowRoot.appendChild(dlg);let cleanup=()=>{dlg.remove();for(let el of this.querySelectorAll("[name]"))el.type==="checkbox"||el.type==="radio"?el.checked=!1:el.tagName==="SELECT"?el.selectedIndex=0:el.value="";this.render()};dlg.addEventListener("close",cleanup),dlg.addEventListener("click",e=>{e.target===dlg&&dlg.close()}),dlg.querySelector("[data-thankyou-close]").addEventListener("click",()=>dlg.close());let stopKeys=e=>e.stopPropagation();dlg.addEventListener("keydown",stopKeys),dlg.addEventListener("keyup",stopKeys),dlg.addEventListener("keypress",stopKeys),dlg.showModal()}_showSingleDownloadReady(url,slug){if(this.hasAttribute("hide-errors"))return;let form=this.shadowRoot.querySelector("form");if(!form)return;form.querySelector(".ready-panel")?.remove();let panel=document.createElement("div");panel.className="ready-panel",panel.setAttribute("role","status"),panel.setAttribute("aria-live","polite");let msg=this._autoDownload?"Your download is starting.":"Your download is ready.";panel.innerHTML=`${escapeHtml(msg)} If it doesn't, <a href="${escapeAttr(url)}" download rel="noopener">click here to download ${escapeHtml(slug)}</a>.`,form.querySelector("button.submit")?.insertAdjacentElement("beforebegin",panel)}_showMultiDownloadReady(urls){if(this.hasAttribute("hide-errors"))return;let form=this.shadowRoot.querySelector("form");if(!form)return;form.querySelector(".ready-panel")?.remove();let panel=document.createElement("div");panel.className="ready-panel",panel.setAttribute("role","status"),panel.setAttribute("aria-live","polite");let msg=this._autoDownload?"Your download is starting. If it doesn\u2019t, download the files individually:":"Your downloads are ready:",items=urls.map(u=>`<li><a href="${escapeAttr(u.url)}" download rel="noopener">${escapeHtml(u.filename||u.slug)}</a></li>`).join("");panel.innerHTML=`${escapeHtml(msg)}<ul class="ready-links">${items}</ul>`,form.querySelector("button.submit")?.insertAdjacentElement("beforebegin",panel)}_showDownloadError(message){if(this.hasAttribute("hide-errors"))return;let override=this.getAttribute("show-errors");override&&override!==""&&override!=="true"&&(message=override);let form=this.shadowRoot.querySelector("form");if(!form)return;let existing=form.querySelector(".ready-panel");if(existing){existing.querySelector(".error-note")?.remove();let note=document.createElement("p");note.className="error-note",note.textContent=message,existing.appendChild(note);return}let panel=document.createElement("div");panel.className="ready-panel error",panel.setAttribute("role","alert"),panel.textContent=message,form.querySelector("button.submit")?.insertAdjacentElement("beforebegin",panel)}async _dynamicDownload(response){let endpoint=this._downloadEndpoint,params=this._downloadParams,token=response.capability_token;if(!endpoint||!token){this.dispatchEvent(new CustomEvent("gem-submit-error",{bubbles:!0,composed:!0,detail:{error:new Error("missing download-endpoint or token"),message:"dynamic download not configured"}}));return}let action=params?`${endpoint}?${params}`:endpoint;this._showDynamicDownloadReady(action,token),this.dispatchEvent(new CustomEvent("gem-download-ready",{bubbles:!0,composed:!0,detail:{kind:"dynamic",endpoint,params,token,response,autoDownload:this._autoDownload}})),this._autoDownload&&await this._postDownload(action,token)}_postDownload(action,token){let request=new Request(action,{method:"POST",body:new URLSearchParams({token}),credentials:"omit"});return this._fetchAndSaveBlob(request,"dynamic")}async _bundleAndSave(urls){this._setDownloading();let ok=!1,errMsg=null,zipName=this.getAttribute("zip-name")||"gem-download.zip";try{let entries=urls.map(u=>({url:u.url,filename:u.filename||u.slug}));await bundleAndDownload(entries,{zipName}),ok=!0}catch(err){errMsg=String(err?.message??err),this._showDownloadError("The automatic download didn\u2019t start \u2014 use the links above to download the files directly."),this.dispatchEvent(new CustomEvent("gem-submit-error",{bubbles:!0,composed:!0,detail:{error:err,message:errMsg,stage:"bundle"}}))}finally{this._setButtonState("idle"),this.dispatchEvent(new CustomEvent("gem-download-complete",{bubbles:!0,composed:!0,detail:{kind:"multi",ok,error:errMsg,filename:ok?zipName:null}}))}}async _fetchAndSaveBlob(request,kind,fallbackFilename){this._setDownloading();let ok=!1,errMsg=null,filename=fallbackFilename||"download";try{let r=await fetch(request);if(!r.ok){let text=await r.text().catch(()=>"");throw new Error(`download ${r.status}: ${text.slice(0,200)}`)}let match=(r.headers.get("content-disposition")||"").match(/filename\*?=(?:UTF-8''|")?([^";]+)"?/i);match&&(filename=decodeURIComponent(match[1]));let blob=await r.blob();triggerBlobDownload(blob,filename),ok=!0}catch(err){errMsg=String(err?.message??err),this._showDownloadError("The automatic download didn\u2019t start \u2014 use the download link above."),this.dispatchEvent(new CustomEvent("gem-submit-error",{bubbles:!0,composed:!0,detail:{error:err,message:errMsg,stage:"download"}}))}finally{this._setButtonState("idle"),this.dispatchEvent(new CustomEvent("gem-download-complete",{bubbles:!0,composed:!0,detail:{kind,ok,error:errMsg,filename:ok?filename:null}}))}}_showDynamicDownloadReady(action,token){if(this.hasAttribute("hide-errors"))return;let form=this.shadowRoot.querySelector("form");if(!form)return;form.querySelector(".ready-panel")?.remove();let panel=document.createElement("div");panel.className="ready-panel",panel.setAttribute("role","status"),panel.setAttribute("aria-live","polite");let msg=this._autoDownload?"Your download is starting.":"Your download is ready.";panel.innerHTML=`${escapeHtml(msg)} If it doesn't, <button type="button" data-resubmit class="inline-link">click here to download</button>.`,form.querySelector("button.submit")?.insertAdjacentElement("beforebegin",panel),panel.querySelector("[data-resubmit]")?.addEventListener("click",()=>{this._postDownload(action,token)})}_setSubmitting(flag){!flag&&this._buttonState==="downloading"||this._setButtonState(flag?"submitting":"idle")}_setDownloading(){this._setButtonState("downloading")}_setButtonState(state){let btn=this.shadowRoot.querySelector("button.submit");if(btn){if(this._buttonState=state,state==="idle"){btn.disabled=!1,btn.setAttribute("aria-busy","false"),btn.dataset.originalLabel&&(btn.textContent=btn.dataset.originalLabel,delete btn.dataset.originalLabel);return}btn.dataset.originalLabel||(btn.dataset.originalLabel=btn.textContent),btn.disabled=!0,btn.setAttribute("aria-busy","true"),btn.textContent=state==="submitting"?"Submitting\u2026":"Downloading\u2026"}}_syncStorageKey(){let form=this.shadowRoot.querySelector("form");if(!form)return;let existing=form.querySelector('[data-check="remember_me"]'),label=this._rememberMeLabel,hasKey=!!this._storageKey,shouldRender=hasKey&&!!label;if(shouldRender&&!existing){let license=form.querySelector('[data-check="license"]'),rm=document.createElement("label");rm.className="check",rm.dataset.check="remember_me";let span=document.createElement("span");span.textContent=label;let input=document.createElement("input");input.type="checkbox",input.name="remember_me",rm.append(input,span),license?.insertAdjacentElement("afterend",rm)}else!shouldRender&&existing&&existing.remove();hasKey&&this._loadPrefill()}_syncCloseButton(){let header=this.shadowRoot.querySelector(".header");if(!header)return;let existing=header.querySelector("[data-close]"),want=this._showClose;if(want&&!existing){let btn=document.createElement("button");btn.type="button",btn.className="close-btn",btn.setAttribute("data-close",""),btn.setAttribute("aria-label","Close"),btn.innerHTML='Close <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>',btn.addEventListener("click",()=>this.close()),header.appendChild(btn)}else!want&&existing&&existing.remove()}_syncModalWrapper(){let root=this.shadowRoot,card=root.querySelector(".card");if(!card)return;let existingDlg=root.querySelector("dialog"),want=this._isModal;if(want===!!existingDlg)return;let focus=this._captureFocus();if(want){let dlg=document.createElement("dialog");dlg.setAttribute("part","dialog"),card.replaceWith(dlg),dlg.appendChild(card),dlg.addEventListener("close",()=>{this.hasAttribute("open")&&this.removeAttribute("open"),this.dispatchEvent(new CustomEvent("gem-close",{bubbles:!0,composed:!0}))}),dlg.addEventListener("click",e=>{e.target===dlg&&this.close()}),this._syncCloseButton(),this.hasAttribute("open")&&this.open()}else existingDlg.open&&existingDlg.close(),existingDlg.replaceWith(card),this._syncCloseButton();this._restoreFocus(focus)}_captureFocus(){let el=this.shadowRoot.activeElement;if(!el)return null;let sel=typeof el.selectionStart=="number"?{start:el.selectionStart,end:el.selectionEnd,dir:el.selectionDirection}:null;return{el,sel}}_restoreFocus(snap){if(!(!snap||!snap.el||!snap.el.isConnected)&&(snap.el.focus(),snap.sel&&typeof snap.el.setSelectionRange=="function"))try{snap.el.setSelectionRange(snap.sel.start,snap.sel.end,snap.sel.dir||"none")}catch{}}reset(){this.shadowRoot.querySelector("form")?.reset()}clearPrefill(){let key=this._storageKey;if(key){try{localStorage.removeItem(key)}catch{}try{sessionStorage.removeItem(key)}catch{}}}};function triggerBlobDownload(blob,filename){let url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url,a.download=filename,a.rel="noopener",document.body.appendChild(a),a.click(),a.remove(),URL.revokeObjectURL(url)}function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c])}function escapeAttr(s){return escapeHtml(s)}function cssEscape(s){return typeof CSS<"u"&&CSS.escape?CSS.escape(s):String(s).replace(/[^a-zA-Z0-9_-]/g,c=>"\\"+c)}(()=>{if(typeof document>"u")return;let href;try{href=new URL("./gem-download-form.css",import.meta.url).href}catch{return}if(document.querySelector("link[data-gem-download-form-css]"))return;let link=document.createElement("link");link.rel="stylesheet",link.href=href,link.dataset.gemDownloadFormCss="",document.head.appendChild(link)})();customElements.define("gem-download-form",GemDownloadForm);
