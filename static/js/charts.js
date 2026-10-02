document.addEventListener("DOMContentLoaded", () => {
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const page = document.body.dataset.page || "";
  const toastRegion = document.getElementById("toast-region");
  let currentUpdateController = null;
  let requestReplacement = async () => {};

  const CITY_STATE_OPTIONS = [
    "Birmingham, AL","Huntsville, AL","Mobile, AL","Montgomery, AL","Anchorage, AK","Fairbanks, AK","Phoenix, AZ","Tucson, AZ","Mesa, AZ","Little Rock, AR","Fayetteville, AR",
    "Los Angeles, CA","San Diego, CA","San Francisco, CA","San Jose, CA","Sacramento, CA","Oakland, CA","Irvine, CA","Long Beach, CA","Fresno, CA","Denver, CO","Boulder, CO","Colorado Springs, CO","Fort Collins, CO",
    "Bridgeport, CT","Hartford, CT","New Haven, CT","Stamford, CT","Wilmington, DE","Dover, DE","Washington, DC","Jacksonville, FL","Miami, FL","Tampa, FL","Orlando, FL","Fort Lauderdale, FL","Tallahassee, FL",
    "Atlanta, GA","Savannah, GA","Augusta, GA","Honolulu, HI","Boise, ID","Chicago, IL","Springfield, IL","Naperville, IL","Indianapolis, IN","Fort Wayne, IN","Des Moines, IA","Cedar Rapids, IA",
    "Wichita, KS","Kansas City, KS","Louisville, KY","Lexington, KY","New Orleans, LA","Baton Rouge, LA","Portland, ME","Baltimore, MD","Annapolis, MD","Frederick, MD","Rockville, MD","Gaithersburg, MD","Columbia, MD","Bel Air, MD",
    "Boston, MA","Cambridge, MA","Worcester, MA","Detroit, MI","Ann Arbor, MI","Grand Rapids, MI","Minneapolis, MN","St. Paul, MN","Jackson, MS","Kansas City, MO","St. Louis, MO","Springfield, MO","Billings, MT","Bozeman, MT",
    "Omaha, NE","Lincoln, NE","Las Vegas, NV","Reno, NV","Manchester, NH","Concord, NH","Newark, NJ","Jersey City, NJ","Princeton, NJ","Trenton, NJ","Albuquerque, NM","Santa Fe, NM","New York, NY","Buffalo, NY","Rochester, NY","Albany, NY",
    "Charlotte, NC","Raleigh, NC","Durham, NC","Greensboro, NC","Wilmington, NC","Fargo, ND","Bismarck, ND","Columbus, OH","Cleveland, OH","Cincinnati, OH","Dayton, OH","Oklahoma City, OK","Tulsa, OK","Portland, OR","Eugene, OR",
    "Philadelphia, PA","Pittsburgh, PA","Harrisburg, PA","Allentown, PA","Providence, RI","Charleston, SC","Columbia, SC","Greenville, SC","Sioux Falls, SD","Rapid City, SD","Nashville, TN","Memphis, TN","Knoxville, TN","Chattanooga, TN",
    "Austin, TX","Dallas, TX","Fort Worth, TX","Houston, TX","San Antonio, TX","Plano, TX","Salt Lake City, UT","Provo, UT","Burlington, VT","Richmond, VA","Arlington, VA","Alexandria, VA","Reston, VA","Tysons, VA","Virginia Beach, VA","Norfolk, VA",
    "Seattle, WA","Bellevue, WA","Tacoma, WA","Spokane, WA","Charleston, WV","Morgantown, WV","Milwaukee, WI","Madison, WI","Green Bay, WI","Cheyenne, WY","Jackson, WY"
  ];

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const codedMessage = (code, message) => `[${code}] ${message}`;

  async function readJsonResponse(response, fallbackMessage, fallbackCode = "E1001") {
    const contentType = response.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) throw new Error(codedMessage(fallbackCode, `${fallbackMessage} (HTTP ${response.status}, ${contentType || "unknown content type"})`));
    try { return await response.json(); } catch { throw new Error(codedMessage("E1002", `${fallbackMessage} (invalid JSON)`)); }
  }
  const serverMessage = (data, fallback, fallbackCode) => codedMessage(data?.error_code || fallbackCode, data?.message || fallback);

  function showToast(message, type = "info", actionLabel = "", actionCallback = null, duration = 4500) {
    if (!toastRegion) return;
    const toast = document.createElement("div"); toast.className = `toast ${type}`;
    const text = document.createElement("span"); text.className = "toast-message"; text.textContent = message; toast.appendChild(text);
    if (actionLabel && typeof actionCallback === "function") {
      const action = document.createElement("button"); action.type = "button"; action.className = "toast-action"; action.textContent = actionLabel;
      action.addEventListener("click", async () => { action.disabled = true; await actionCallback(); dismiss(); }); toast.appendChild(action);
    }
    toastRegion.appendChild(toast); const timer = setTimeout(dismiss, duration);
    function dismiss() { clearTimeout(timer); if (!toast.isConnected) return; toast.classList.add("is-hiding"); setTimeout(() => toast.remove(), 190); }
  }

  const pendingToast = sessionStorage.getItem("jobFinderToast");
  if (pendingToast) { sessionStorage.removeItem("jobFinderToast"); try { const data = JSON.parse(pendingToast); showToast(data.message, data.type || "info"); } catch { showToast(pendingToast); } }
  const yearElement = $("#copyright-year"); if (yearElement) yearElement.textContent = new Date().getFullYear();

  $$("datalist#city-state-options").forEach((list) => { list.innerHTML = CITY_STATE_OPTIONS.map((city) => `<option value="${city}"></option>`).join(""); });
  // As you type in a city box, the list fills with every matching U.S. place (Census list), near home first.
  $$('input[list="city-state-options"]').forEach((input) => {
    const list = document.getElementById("city-state-options");
    let timer = null, asked = "";
    input.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const typed = input.value.trim();
        if (typed.length < 2 || typed === asked) return;
        asked = typed;
        try {
          const data = await (await fetch(`/city-matches?q=${encodeURIComponent(typed)}`, { cache: "no-store" })).json();
          if (input.value.trim() !== typed || !data.matches?.length) return;
          list.replaceChildren(...data.matches.map((place) => new Option(place)));
        } catch { /* keep the built-in list */ }
      }, 150);
    });
  });


  $$(".details-action").forEach((button) => button.addEventListener("click", () => {
    const target = document.getElementById(button.dataset.detailsTarget); if (!target) return;
    const opening = target.hidden; target.hidden = !opening; button.textContent = opening ? "Hide Details" : "View Details";
  }));

  $$(".history-cities").forEach((el) => { try { const items = JSON.parse(el.dataset.cities || "[]"); el.textContent = items.map((x) => `${x.city} · ${x.radius ?? x.radius_miles ?? 50} miles`).join(" • "); } catch { el.textContent = ""; } });
  $$(".search-again-button").forEach((button) => button.addEventListener("click", () => {
    const params = new URLSearchParams({ job_title: button.dataset.jobTitle || "", state: button.dataset.state || "", cities: button.dataset.cities || "[]" });
    window.location.href = `/?${params.toString()}`;
  }));

  async function saveListing(button) {
    const companyId = button.dataset.companyId; if (!companyId || button.disabled) return;
    button.disabled = true; button.classList.add("is-saving"); button.textContent = "Adding…";
    try {
      const response = await fetch("/save-kept", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken }, body: JSON.stringify({ company_ids: [companyId] }) });
      const data = await readJsonResponse(response, "Could not save this result.", "E1201"); if (!response.ok) throw new Error(serverMessage(data, "Could not save this result.", "E1201"));
      button.classList.remove("is-saving"); button.classList.add("is-saved"); button.textContent = "Saved"; button.disabled = false;
      const row = button.closest(".result-row"); if (row) row.dataset.saved = "true";
      showToast("Added to Dashboard", "success");
    } catch (error) { button.disabled = false; button.classList.remove("is-saving"); button.textContent = "Keep"; showToast(error.message, "danger"); }
  }
  async function unsaveListing(button) {
    const companyId = button.dataset.companyId; if (!companyId || button.disabled) return;
    button.disabled = true; button.textContent = "Unsaving…";
    try {
      const response = await fetch(`/unsave-kept/${companyId}`, { method: "POST", headers: { "X-CSRF-Token": csrfToken, Accept: "application/json" } });
      const data = await readJsonResponse(response, "Could not unsave this result.", "E3102"); if (!response.ok) throw new Error(serverMessage(data, "Could not unsave this result.", "E3102"));
      button.classList.remove("is-saved"); button.textContent = "Keep"; button.disabled = false; const row = button.closest(".result-row"); if (row) row.dataset.saved = "false"; showToast("Removed from saved jobs", "info");
    } catch (error) { button.disabled = false; button.textContent = "Saved"; showToast(error.message, "danger"); }
  }
  $$(".keep-action").forEach((button) => {
    button.addEventListener("mouseenter", () => { if (button.classList.contains("is-saved") && !button.disabled) button.textContent = "Unsave"; });
    button.addEventListener("mouseleave", () => { if (button.classList.contains("is-saved") && !button.disabled) button.textContent = "Saved"; });
    button.addEventListener("click", () => button.classList.contains("is-saved") ? unsaveListing(button) : saveListing(button));
  });

  async function restoreRejected(companyId) {
    const response = await fetch(`/restore-rejected/${companyId}`, { method: "POST", headers: { "X-CSRF-Token": csrfToken, "X-Requested-With": "fetch", Accept: "application/json" } });
    const data = await readJsonResponse(response, "Could not restore the listing.", "E3204"); if (!response.ok) throw new Error(serverMessage(data, "Could not restore the listing.", "E3204")); return data;
  }
  $$(".reject-action").forEach((button) => button.addEventListener("click", async () => {
    const companyId = button.dataset.companyId, companyName = button.dataset.companyName || "this listing";
    if (!confirm(`Reject ${companyName}? This hides it from search. You can restore it from Settings.`)) return;
    button.disabled = true; button.classList.add("is-removing"); button.textContent = "Removed";
    try {
      const reason=button.closest(".result-row")?.nextElementSibling?.querySelector(".reject-reason")?.value||"other";
      const response = await fetch(`/reject-listing/${companyId}`, { method: "POST", headers: { "X-CSRF-Token": csrfToken, "X-Requested-With": "fetch", "Content-Type":"application/json", Accept: "application/json" }, body: JSON.stringify({reason}) });
      const data = await readJsonResponse(response, "Could not reject the listing.", "E3202"); if (!response.ok) throw new Error(serverMessage(data, "Could not reject the listing.", "E3202"));
      const item = button.closest(".result-row, .saved-job-card"); if (item) { item.classList.add("is-removing"); setTimeout(() => { if (item.matches("tr")) { const next = item.nextElementSibling; if (next?.classList.contains("details-row")) next.remove(); } item.remove(); updateClientResults(); }, 330); }
      if(page==="search")requestReplacement();
      showToast("Moved to Settings", "danger", "Undo", async () => { try { await restoreRejected(companyId); sessionStorage.setItem("jobFinderToast", JSON.stringify({ message: "Listing restored", type: "success" })); window.location.reload(); } catch (error) { showToast(error.message, "danger"); } }, 6500);
    } catch (error) { button.disabled = false; button.classList.remove("is-removing"); button.textContent = "Reject Listing"; showToast(error.message, "danger"); }
  }));
  $$(".restore-action").forEach((button) => button.addEventListener("click", async () => { if (!confirm(`Restore ${button.dataset.companyName || "this listing"}?`)) return; button.disabled = true; try { const data = await restoreRejected(button.dataset.companyId); button.closest(".saved-job-card")?.remove(); showToast(data.kept ? "Restored to Dashboard" : "Restored to search results", "success"); } catch (error) { button.disabled = false; showToast(error.message, "danger"); } }));
  $$(".block-action").forEach((button) => button.addEventListener("click", async () => {
    const companyId = button.dataset.companyId, domain = button.dataset.domain || "this domain"; if (!companyId || !confirm(`Block ${domain} from future searches? This blocks the entire website, not just this listing.`)) return;
    button.disabled = true; try { const response = await fetch(`/block-domain/${companyId}`, { method: "POST", headers: { "X-CSRF-Token": csrfToken, "X-Requested-With": "fetch", Accept: "application/json" } }); const data = await readJsonResponse(response, "Could not block the domain.", "E3211"); if (!response.ok) throw new Error(serverMessage(data, "Could not block the domain.", "E3211")); button.textContent = "Blocked";
      if(page==="search") { fadeResultRows(row => (row.dataset.companyId === String(companyId)) || (row.querySelector('td[data-label="Domain"]')?.textContent||"").trim().toLowerCase().endsWith(data.domain.toLowerCase())); requestReplacement(); }
      showToast(`${data.domain || domain} blocked from future searches`, "info"); } catch (error) { button.disabled = false; showToast(error.message, "danger"); }
  }));

  $$(".block-company-action").forEach((button) => button.addEventListener("click", async () => {
    const companyId=button.dataset.companyId, name=button.dataset.companyName||"this company";
    if(!companyId||!confirm(`Block ${name} from future searches?`))return;
    button.disabled=true;
    try{
      const response=await fetch(`/block-company/${companyId}`,{method:"POST",headers:{"X-CSRF-Token":csrfToken,"X-Requested-With":"fetch",Accept:"application/json"}});
      const data=await readJsonResponse(response,"Could not block the company.","E3221");
      if(!response.ok)throw new Error(serverMessage(data,"Could not block the company.","E3221"));
      button.textContent="Company Blocked";
      if(page==="search") { fadeResultRows(row => (row.dataset.company||"").toLowerCase() === (data.company||name).toLowerCase()); requestReplacement(); }
      showToast(`${data.company||name} blocked from future searches`,"info");
    }catch(error){button.disabled=false;showToast(error.message,"danger");}
  }));

  function fadeResultRows(matches) {
    const rows=$$("#results-table .result-row").filter(matches);
    rows.forEach(row=>row.classList.add("is-removing"));
    setTimeout(()=>{
      rows.forEach(row=>{const details=row.nextElementSibling;if(details?.classList.contains("details-row"))details.remove();row.remove();});
      updateClientResults();
    },330);
  }

  const STATE_NAMES = {"AK":"Alaska","AL":"Alabama","AR":"Arkansas","AZ":"Arizona","CA":"California","CO":"Colorado","CT":"Connecticut","DC":"District of Columbia","DE":"Delaware","FL":"Florida","GA":"Georgia","HI":"Hawaii","IA":"Iowa","ID":"Idaho","IL":"Illinois","IN":"Indiana","KS":"Kansas","KY":"Kentucky","LA":"Louisiana","MA":"Massachusetts","MD":"Maryland","ME":"Maine","MI":"Michigan","MN":"Minnesota","MO":"Missouri","MS":"Mississippi","MT":"Montana","NC":"North Carolina","ND":"North Dakota","NE":"Nebraska","NH":"New Hampshire","NJ":"New Jersey","NM":"New Mexico","NV":"Nevada","NY":"New York","OH":"Ohio","OK":"Oklahoma","OR":"Oregon","PA":"Pennsylvania","RI":"Rhode Island","SC":"South Carolina","SD":"South Dakota","TN":"Tennessee","TX":"Texas","UT":"Utah","VA":"Virginia","VT":"Vermont","WA":"Washington","WI":"Wisconsin","WV":"West Virginia","WY":"Wyoming"};
  const STATE_CODES = Object.fromEntries(Object.entries(STATE_NAMES).map(([code,name])=>[name.toLowerCase(),code]));
  function stateCode(value){
    const raw=(value||"").trim();
    const upper=raw.toUpperCase();
    return STATE_NAMES[upper]?upper:STATE_CODES[raw.toLowerCase()]||upper;
  }
  const resultFilter = $("#result-filter"), resultStateFilter = $("#result-state-filter"), resultArrangementFilter = $("#result-arrangement-filter"), resultStatusFilter = $("#result-status-filter"), resultSort = $("#result-sort"), resultMinCredibility = $("#result-min-credibility"), resultScheduleFilter = $("#result-schedule-filter"), resultNewFilter = $("#result-new-filter"), resultsCount = $("#results-count"), resultsTableBody = $("#results-table tbody");
  function updateClientResults() {
    if (!resultsTableBody) return;
    const query=(resultFilter?.value||"").trim().toLowerCase(),status=resultStatusFilter?.value||"all";
    const selectedState=resultStateFilter?.value||"all";
    const selectedArrangement=resultArrangementFilter?.value||"all";
    const minScore=Number(resultMinCredibility?.value||0), selectedSchedule=resultScheduleFilter?.value||"all";
    const queryState=STATE_NAMES[query.toUpperCase()]?query.toUpperCase():STATE_CODES[query]||null;
    let rows=$$(".result-row",resultsTableBody);
    rows.forEach((row)=>{
      const rowState=stateCode(row.dataset.state);
      const haystack=`${row.dataset.company||""} ${row.dataset.job||""} ${row.dataset.city||""}`.toLowerCase();
      const textMatches=!query||(queryState?rowState===queryState:haystack.includes(query));
      const arrangement=row.dataset.workArrangement||"";
      const score=Number(row.dataset.careerCredibility||0), schedule=(row.dataset.schedule||"").toLowerCase();
      const matches=textMatches&&(selectedState==="all"||rowState===selectedState)
        &&(selectedArrangement==="all"||arrangement===selectedArrangement||(selectedArrangement==="unknown"&&!arrangement))
        &&(status==="all"||row.dataset.status===status)
        &&(minScore===-1||score>=Math.max(3,minScore))
        &&(selectedSchedule==="all"||(selectedSchedule==="unknown"?!schedule:(selectedSchedule==="freelance"?/freelanc|gig|project.based|independent contractor/.test(schedule):schedule.includes(selectedSchedule))))
        &&(resultNewFilter?.value!=="new"||row.dataset.new==="true");
      row.hidden=!matches;
      const details=row.nextElementSibling;
      if(details?.classList.contains("details-row")&&row.hidden)details.hidden=true;
    });
    const sortValue = resultSort?.value || "default"; rows.sort((a,b) => sortValue === "company" ? (a.dataset.company||"").localeCompare(b.dataset.company||"") : sortValue === "job" ? (a.dataset.job||"").localeCompare(b.dataset.job||"") : sortValue === "distance" ? Number(a.dataset.distance||99999)-Number(b.dataset.distance||99999) : sortValue === "career-credibility" ? Number(b.dataset.careerCredibility||0)-Number(a.dataset.careerCredibility||0) : sortValue === "usa-credibility" ? Number(b.dataset.usaCredibility||0)-Number(a.dataset.usaCredibility||0) : 0);
    rows.forEach((row) => { const details = row.nextElementSibling; resultsTableBody.appendChild(row); if (details?.classList.contains("details-row")) resultsTableBody.appendChild(details); });
    const visible = rows.filter((row) => !row.hidden).length; if (resultsCount) resultsCount.textContent = `${visible} result${visible === 1 ? "" : "s"}`;
  }
  if(resultStateFilter&&resultsTableBody){
    const codes=[...new Set($$(".result-row",resultsTableBody).map(row=>stateCode(row.dataset.state)).filter(Boolean))];
    codes.sort((a,b)=>(STATE_NAMES[a]||a).localeCompare(STATE_NAMES[b]||b));
    codes.forEach(code=>{const option=document.createElement("option");option.value=code;option.textContent=STATE_NAMES[code]?`${STATE_NAMES[code]} (${code})`:code;resultStateFilter.appendChild(option);});
  }
  [resultFilter,resultStateFilter,resultArrangementFilter,resultStatusFilter,resultSort,resultMinCredibility,resultScheduleFilter,resultNewFilter].forEach(control=>control?.addEventListener("input",updateClientResults));
  [resultStateFilter,resultArrangementFilter,resultStatusFilter,resultSort,resultMinCredibility,resultScheduleFilter,resultNewFilter].forEach(control=>control?.addEventListener("change",updateClientResults));
  updateClientResults();

  function normalizeJobTitles(value) {
    const seen = new Set();
    return value.split(",").map((v) => v.trim()).filter((v) => {
      if (!v) return false;
      const key = v.toLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    }).join(", ");
  }
  function setupCityEditor({ listId,inputId,radiusId,addId,initial=[],storageKey=null,hiddenId=null }) {
    const list=$("#"+listId), input=$("#"+inputId), radius=$("#"+radiusId), add=$("#"+addId), hidden=hiddenId?$("#"+hiddenId):null; if(!list||!input||!radius||!add) return {getItems:()=>[]};
    let items=Array.isArray(initial)?initial.map((i)=>({city:String(i.city||"").trim(),radius:Number(i.radius??i.radius_miles??50),scope:STATE_CODES[String(i.city||"").trim().toLowerCase()]?"state":"city"})).filter((i)=>i.city):[];
    const persist=()=>{ if(storageKey)localStorage.setItem(storageKey,JSON.stringify(items)); if(hidden)hidden.value=JSON.stringify(items); };
    function render(){ list.innerHTML=""; items.forEach((item,index)=>{ const chip=document.createElement("div"); chip.className="city-chip"; const label=document.createElement("span"); label.textContent=item.scope==="state"?`${item.city} · statewide`:`${item.city} · ${item.radius} miles`; chip.appendChild(label); const remove=document.createElement("button"); remove.type="button"; remove.className="city-remove"; remove.textContent="×"; remove.addEventListener("click",()=>{items.splice(index,1);persist();render();}); chip.appendChild(remove); list.appendChild(chip); }); persist(); }
    function addCity(){ const city=input.value.trim(); if(!city)return; const r=Number(radius.value||50); const existing=items.find((i)=>i.city.toLowerCase()===city.toLowerCase()); if(existing)existing.radius=r; else items.push({city,radius:r,scope:STATE_CODES[city.toLowerCase()]?"state":"city"}); input.value=""; radius.disabled=false; render(); }
    input.addEventListener("input",()=>{radius.disabled=Boolean(STATE_CODES[input.value.trim().toLowerCase()]);});
    add.addEventListener("click",addCity); input.addEventListener("keydown",(e)=>{if(e.key==="Enter"){e.preventDefault();addCity();}}); render(); return {getItems:()=>items.slice(),addPending:addCity,setItems:(v)=>{items=Array.isArray(v)?v:[];render();}};
  }
  let searchCitiesInitial=[]; try { const q=new URLSearchParams(location.search).get("cities"), local=localStorage.getItem("jobFinderCities"), profile=$("#saved-profile-cities")?.textContent||"[]"; searchCitiesInitial=q?JSON.parse(q):local?JSON.parse(local):JSON.parse(profile); } catch {}
  const searchCityEditor=setupCityEditor({listId:"search-city-list",inputId:"search-city-input",radiusId:"search-city-radius",addId:"add-search-city",initial:searchCitiesInitial,storageKey:"jobFinderCities"});
  let profileCitiesInitial=[]; try{profileCitiesInitial=JSON.parse($("#profile-cities-json")?.value||"[]");}catch{} setupCityEditor({listId:"profile-city-list",inputId:"profile-city-input",radiusId:"profile-city-radius",addId:"add-profile-city",initial:profileCitiesInitial,hiddenId:"profile-cities-json"});

  const jobTitleInput=$("#job-title"), stateInput=$("#search-state"), jobTitleSuggestions=$("#job-title-suggestions");
  if(jobTitleInput){ const params=new URLSearchParams(location.search); const profileTitles=(()=>{try{return JSON.parse($("#saved-profile-titles")?.textContent||"[]").join(", ");}catch{return "";}})(); jobTitleInput.value=params.get("job_title")||profileTitles||localStorage.getItem("jobFinderJobTitle")||jobTitleInput.value||""; }
  if(stateInput){ const params=new URLSearchParams(location.search); stateInput.value=params.get("state")||stateInput.value||localStorage.getItem("jobFinderState")||""; }
  async function loadJobTitleSuggestions(){ if(!jobTitleInput||!jobTitleSuggestions)return; const titles=normalizeJobTitles(jobTitleInput.value); if(!titles){jobTitleSuggestions.hidden=true;jobTitleSuggestions.innerHTML="";return;} try{const response=await fetch(`/job-title-suggestions?titles=${encodeURIComponent(titles)}`,{cache:"no-store"}); const data=await readJsonResponse(response,"Could not load related job-title suggestions.","E1101"); if(!response.ok)throw new Error(serverMessage(data,"Could not load related job-title suggestions.","E1101")); jobTitleSuggestions.innerHTML=""; (data.suggestions||[]).forEach((suggestion)=>{const b=document.createElement("button");b.type="button";b.className="job-suggestion-button";b.textContent=`+ ${suggestion}`;b.addEventListener("click",()=>{jobTitleInput.value=normalizeJobTitles(`${jobTitleInput.value}, ${suggestion}`);localStorage.setItem("jobFinderJobTitle",jobTitleInput.value);});jobTitleSuggestions.appendChild(b);}); jobTitleSuggestions.hidden=!jobTitleSuggestions.children.length;}catch(e){console.error(e);jobTitleSuggestions.hidden=true;}}
  let titleSuggestionTimer=null; jobTitleInput?.addEventListener("input",()=>{clearTimeout(titleSuggestionTimer); titleSuggestionTimer=setTimeout(loadJobTitleSuggestions,600);}); jobTitleInput?.addEventListener("blur",()=>{ if(jobTitleInput.value.trim()){ clearTimeout(titleSuggestionTimer); titleSuggestionTimer=setTimeout(loadJobTitleSuggestions,350); }});

  const searchFormError=$("#search-form-error"); function showFormError(message=""){ if(!searchFormError)return; searchFormError.textContent=message; searchFormError.hidden=!message; }
  const loadingPanel=$("#loading-panel"), loadingTitle=$("#loading-title"), loadingDetail=$("#loading-detail"), loadingStop=$("#loading-stop");
  function showLoading(title,detail="",allowStop=false){ if(!loadingPanel)return; loadingTitle.textContent=title; loadingDetail.textContent=detail; loadingStop.hidden=!allowStop; loadingPanel.hidden=false; }
  function hideLoading(){ if(loadingPanel)loadingPanel.hidden=true; if(loadingStop)loadingStop.onclick=null; }

  function showStatusModal(title,message,tone="info",{loading=false,stopLabel="",onStop=null,closable=true,confirmLabel="OK",onConfirm=null}={}){
    let modal=$("#status-modal"); if(!modal){modal=document.createElement("div");modal.id="status-modal";modal.className="status-modal";document.body.appendChild(modal);} modal.dataset.tone=tone; modal.hidden=false;
    modal.innerHTML=`<div class="status-modal-backdrop"></div><div class="status-modal-card" role="dialog" aria-modal="true"><div class="${loading?"color-spinner":"status-modal-icon"}" aria-hidden="true"></div><h2>${title}</h2><p>${message}</p><div class="modal-actions">${stopLabel?`<button class="bordered-button secondary-action modal-stop" type="button">${stopLabel}</button>`:""}${closable?`<button class="bordered-button primary-action modal-close" type="button">${confirmLabel}</button>`:""}</div></div>`;
    $(".modal-close",modal)?.addEventListener("click",()=>{modal.hidden=true;if(onConfirm)onConfirm();}); $(".status-modal-backdrop",modal)?.addEventListener("click",()=>{if(closable)modal.hidden=true;}); $(".modal-stop",modal)?.addEventListener("click",async()=>{if(onStop)await onStop();modal.hidden=true;});
  }
  async function fetchWithTimeout(url,options={},timeoutMs=8000,externalController=null){const controller=externalController||new AbortController();const timer=setTimeout(()=>controller.abort(),timeoutMs);try{return await fetch(url,{...options,signal:controller.signal});}finally{clearTimeout(timer);}}

  function setupPagination(listSelector,itemSelector,controlsSelector,searchSelector) {
    const list=$(listSelector),controls=$(controlsSelector),search=$(searchSelector);
    if(!list||!controls)return;
    let page=0;
    const size=Number(list.dataset.pageSize)||10;
    function render(){
      const all=$$(itemSelector,list).filter(item=>item.isConnected);
      const query=(search?.value||"").trim().toLocaleLowerCase();
      const matches=all.filter(item=>item.textContent.toLocaleLowerCase().includes(query));
      const total=Math.ceil(matches.length/size);
      page=Math.max(0,Math.min(page,total-1));
      all.forEach(item=>{item.hidden=true;});
      matches.slice(page*size,(page+1)*size).forEach(item=>{item.hidden=false;});
      controls.replaceChildren();
      if(!matches.length){
        const message=document.createElement("span");message.textContent=query?"No matches found.":"No items to show.";
        controls.appendChild(message);return;
      }
      if(total<=1)return;
      const previous=document.createElement("button"),next=document.createElement("button"),label=document.createElement("span");
      previous.type=next.type="button";previous.className=next.className="bordered-button secondary-action";
      previous.textContent="Previous";next.textContent="Next";label.textContent=`Page ${page+1} of ${total}`;
      previous.disabled=page===0;next.disabled=page===total-1;
      previous.addEventListener("click",()=>{page--;render();});
      next.addEventListener("click",()=>{page++;render();});
      controls.append(previous,label,next);
    }
    search?.addEventListener("input",()=>{page=0;render();});
    render();
    new MutationObserver(render).observe(list,{childList:true});
  }
  setupPagination(".rejected-grid",".rejected-card",'[data-pagination="rejected-grid"]',"#rejected-search");
  setupPagination(".blocked-domain-list:not(.blocked-company-list)","li",'[data-pagination="blocked-domain-list"]',"#blocked-domain-search");
  setupPagination(".blocked-company-list","li",'[data-pagination="blocked-company-list"]',"#blocked-company-search");
  setupPagination(".search-skip-list","li",'[data-pagination="search-skip-list"]',"#search-skip-search");

  // Dashboard profile controls. Uploaded documents are parsed in memory and are not retained.
  const profileForm = $("#profile-form");
  if (profileForm) {
    // Suggested titles under "Other Job Titles" while the user isn't typing there; a click adds one.
    const titles = $("#job-titles"), primaryTitle = $("#primary-job-title"), titleChips = $("#title-suggestion-chips");
    let chipsTimer = null;
    async function refreshTitleChips() {
      if (!titleChips) return;
      const current = [primaryTitle?.value || "", ...titles.value.split(",")].map((t) => t.trim()).filter(Boolean);
      const typing = document.activeElement === titles && titles.value.split(",").pop().trim();
      if (!current.length || typing) { titleChips.hidden = true; return; }
      try {
        const response = await fetch(`/job-title-suggestions?titles=${encodeURIComponent(current.join(", "))}`, { cache: "no-store" });
        const data = await response.json();
        const list = titleChips.querySelector(".chip-row");
        list.replaceChildren(...(data.suggestions || []).map((title) => {
          const b = document.createElement("button"); b.type = "button"; b.className = "job-suggestion-button"; b.textContent = `+ ${title}`;
          b.addEventListener("click", () => {
            const kept = titles.value.split(",").map((t) => t.trim()).filter(Boolean);
            titles.value = [...kept, title].join(", ");
            refreshTitleChips();
          });
          return b;
        }));
        titleChips.hidden = !list.children.length;
      } catch { titleChips.hidden = true; }
    }
    const queueChips = () => { clearTimeout(chipsTimer); chipsTimer = setTimeout(refreshTitleChips, 400); };
    titles.addEventListener("input", queueChips); titles.addEventListener("blur", queueChips);
    primaryTitle?.addEventListener("change", queueChips); primaryTitle?.addEventListener("blur", queueChips);
    refreshTitleChips();
    const skillsField = $("#skills-json"), historyField = $("#work-history-json"), skillsList = $("#profile-skill-list"), historyList = $("#work-history-list");
    let skills = [], history = [];
    try { skills = JSON.parse(skillsField.value); } catch {}
    try { history = JSON.parse(historyField.value); } catch {}
    if (!Array.isArray(skills)) skills = [];
    if (!Array.isArray(history)) history = [];
    function renderSkills() {
      skillsList.replaceChildren();
      skills.forEach((skill, index) => {
        const chip = document.createElement("span"); chip.className = "skill-chip";
        const label = document.createElement("span"); label.textContent = skill;
        const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "×"; remove.setAttribute("aria-label", `Remove ${skill}`);
        remove.addEventListener("click", () => { skills.splice(index, 1); renderSkills(); showRelatedSkills(); });
        chip.append(label, remove); skillsList.append(chip);
      }); skillsField.value = JSON.stringify(skills); showResumeSkills();
    }
    // Suggestion chips under Skills: tap the ones you have (Save Profile keeps them). One group for skills the uploaded
    // résumé mentions, one for skills the jobs Job Finder found keep asking for.
    function suggestionChips(box, heading, hint, choices) {
      box.replaceChildren();
      const open = choices.filter(choice => !skills.some(saved => saved.toLowerCase() === choice.skill.toLowerCase()));
      if (!open.length) return;
      const title = document.createElement("h4"); title.textContent = heading; title.title = hint; box.append(title);
      for (const choice of open) {
        const button = document.createElement("button"); button.type = "button"; button.className = "bordered-button secondary-action";
        button.textContent = `+ ${choice.skill}${choice.note ? ` (${choice.note})` : ""}`;
        button.addEventListener("click", () => { addSkill(choice.skill); showRelatedSkills(); });
        box.append(button);
      }
      if (open.length > 1) {
        const all = document.createElement("button"); all.type = "button"; all.className = "bordered-button primary-action"; all.textContent = "Add all";
        all.addEventListener("click", () => { open.forEach(choice => addSkill(choice.skill)); showRelatedSkills(); });
        box.append(all);
      }
    }
    function showResumeSkills() {
      const box = $("#resume-skill-suggestions"); if (!box) return;
      let found = []; try { found = JSON.parse(box.dataset.skills || "[]"); } catch {}
      suggestionChips(box, "Suggestions from Resume:", `Skills found in your résumé (${box.dataset.source || "uploaded résumé"}). Add the ones you have, then Save Profile.`,
        found.map(skill => ({ skill })));
      const demand = $("#demand-skill-suggestions"); if (!demand) return;
      let asked = []; try { asked = JSON.parse(demand.dataset.skills || "[]"); } catch {}
      const fromResume = new Set(found.map(skill => skill.toLowerCase()));
      suggestionChips(demand, "Suggestions:", "Often asked for in the jobs Job Finder found for you (the number is how many). Add only what you have, then Save Profile.",
        asked.filter(item => !fromResume.has(String(item.skill).toLowerCase()))
          .map(item => ({ skill: item.skill, note: `${item.count} job${item.count === 1 ? "" : "s"}` })));
    }
    function addSkill(skill) { const value = (skill || "").trim().slice(0, 80); if (value && !skills.some(s => s.toLowerCase() === value.toLowerCase())) { skills.push(value); renderSkills(); } }
    const relatedSkills = {HTML:["CSS","JavaScript","Responsive Design","Accessibility"],CSS:["Sass","Bootstrap","Responsive Design"],JavaScript:["TypeScript","React","jQuery"],"Web Design":["UI Design","UX Design","Figma"],WordPress:["PHP","SEO","Content Management"],"Email Marketing":["Salesforce Marketing Cloud","Litmus"],Git:["GitHub","Docker"]};
    function showRelatedSkills() {
      const container=$("#related-skill-suggestions"); if(!container)return;container.replaceChildren();
      const choices=[...new Set(skills.flatMap(skill=>relatedSkills[skill]||[]))].filter(skill=>!skills.some(saved=>saved.toLowerCase()===skill.toLowerCase())).slice(0,8);
      if(!choices.length)return;const heading=document.createElement("small");heading.textContent="Related skills to consider (add only if you have them):";container.append(heading);
      choices.forEach(skill=>{const button=document.createElement("button");button.type="button";button.className="bordered-button secondary-action";button.textContent=`+ ${skill}`;button.addEventListener("click",()=>{addSkill(skill);showRelatedSkills();});container.append(button);});
    }
    $("#add-skill").addEventListener("click", () => { addSkill($("#new-skill").value); $("#new-skill").value = ""; showRelatedSkills(); });
    $("#new-skill").addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); $("#add-skill").click(); } });
    // Work history: one card per job (title, company, dates and the first point visible), opened to edit.
    const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const monthLabel = value => { const m = /^(\d{4})-(\d{2})$/.exec(value || ""); return m ? `${MONTHS[Number(m[2]) - 1] || m[2]} ${m[1]}` : (value || ""); };
    const datesLabel = text => {
      const parts = String(text || "").split(/\s*[–-]\s*(?=\d{4}|Present)/i);
      return parts.length === 2 ? `${monthLabel(parts[0])} – ${/present/i.test(parts[1]) ? "Present" : monthLabel(parts[1])}` : String(text || "");
    };
    const firstPoint = text => (String(text || "").split("\n").map(line => line.replace(/^\s*[•\-*]\s*/, "").trim()).find(Boolean) || "");
    function el(tag, className, text) { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; }
    function renderHistory() {
      historyList.replaceChildren();
      if (!history.length) historyList.append(el("p", "work-empty", "No jobs added yet. Add one, or upload a résumé to fill these in."));
      history.forEach((job, index) => {
        const details = el("details", "work-entry");
        const summary = el("summary");
        const main = el("span", "work-main"), role = el("strong", "work-role"), company = el("span", "work-company"), dates = el("span", "work-dates"), preview = el("span", "work-preview");
        main.append(role, company); summary.append(main, dates, preview); details.append(summary);
        const refresh = () => {
          role.textContent = job.role || "New role"; company.textContent = job.company || "";
          const label = datesLabel(job.dates); dates.textContent = label; dates.hidden = !label;
          preview.textContent = firstPoint(job.description); preview.hidden = !preview.textContent;
        };
        refresh();
        const save = () => { historyField.value = JSON.stringify(history); refresh(); };
        const fields = el("div", "work-fields");
        const field = (labelText, key, example, area) => {
          const label = el("label", area ? "work-wide" : ""); label.append(labelText + " ");
          const input = el(area ? "textarea" : "input"); input.value = job[key] || ""; input.placeholder = example;
          input.maxLength = key === "description" ? 3000 : key === "dates" ? 100 : 150; if (area) input.rows = 6;
          input.addEventListener("input", () => { job[key] = input.value; save(); });
          label.append(input); return { label, input };
        };
        const title = field("Job Title", "role", "e.g. Web Designer"), companyField = field("Company", "company", "e.g. Acme Widgets");
        fields.append(title.label, companyField.label);
        // Dates: month pickers when the dates fit them; the typed text too when they don't (e.g. just years).
        const datesField = field("Dates as written", "dates", "e.g. 2014 – 2015"); datesField.label.className = "work-wide";
        const known = (job.dates || "").match(/^(\d{4}-\d{2})\s*[–-]\s*(\d{4}-\d{2}|Present)$/i);
        const months = el("div", "history-months");
        const start = el("input"), end = el("input"), current = el("input"); start.type = end.type = "month"; current.type = "checkbox";
        if (known) { start.value = known[1]; if (known[2].toLowerCase() === "present") current.checked = true; else end.value = known[2]; }
        end.disabled = current.checked;
        const sync = () => { if (!start.value) return; job.dates = `${start.value} – ${current.checked ? "Present" : end.value || ""}`.trim(); datesField.input.value = job.dates; end.disabled = current.checked; save(); };
        [start, end, current].forEach(control => control.addEventListener("change", sync));
        for (const [caption, control] of [["Start month", start], ["End month", end], ["Current job", current]]) { const box = el("label"); box.append(control, caption); months.append(box); }
        const dateBlock = el("fieldset", "work-dates-editor"); dateBlock.append(el("legend", "", "Dates"), months);
        if (job.dates && !known) dateBlock.append(datesField.label);
        fields.append(dateBlock);
        const description = field("What you did", "description", "e.g. Built responsive pages for client websites", true);
        description.label.append(el("small", "field-help", "One point per line."));
        fields.append(description.label);
        const actions = el("div", "work-actions");
        const remove = el("button", "bordered-button destructive-action", "Remove Job"); remove.type = "button";
        remove.addEventListener("click", () => { history.splice(index, 1); renderHistory(); });
        const done = el("button", "bordered-button secondary-action", "Done"); done.type = "button";
        done.addEventListener("click", () => { details.open = false; details.scrollIntoView({ block: "nearest" }); });
        actions.append(remove, done); fields.append(actions); details.append(fields); historyList.append(details);
      }); historyField.value = JSON.stringify(history);
    }
    $("#add-work-history").addEventListener("click", () => { history.push({role:"",company:"",dates:"",description:""}); renderHistory(); historyList.lastElementChild.open = true; });
    // No work history saved yet: fill the form in from the uploaded résumé. Nothing is saved until Save Profile.
    const historyNote = $("#work-history-note");
    if (historyNote && !history.length) {
      let fromResume = []; try { fromResume = JSON.parse(historyNote.dataset.history || "[]"); } catch {}
      if (Array.isArray(fromResume) && fromResume.length) {
        history = fromResume.map(job => ({ role: job.role || "", company: job.company || "", dates: job.dates || "", description: job.description || "" }));
        historyNote.textContent = `Filled in from your résumé (${historyNote.dataset.source || "uploaded résumé"}): check each job, remove any you don't want, then Save Profile.`;
        historyNote.hidden = false;
      }
    }
    renderSkills(); renderHistory();
    $("#home-location").addEventListener("input", () => { const value = $("#home-location").value.trim(); $("#profile-state").value = value.includes(",") ? value.split(",").pop().trim() : ""; });
    const resumeFile = $("#resume-file"), resumeDialog = $("#resume-review"), resumeContent = $("#resume-review-content");
    $("#upload-resume").addEventListener("click", () => resumeFile.click());
    $("#cancel-resume-review").addEventListener("click", () => resumeDialog.close());
    let suggestions = null;
    resumeFile.addEventListener("change", async () => {
      const file = resumeFile.files[0]; if (!file) return;
      if (file.size > 5 * 1024 * 1024) { showToast("Résumé must be under 5 MB.", "danger"); resumeFile.value = ""; return; }
      const body = new FormData(); body.append("resume", file);
      showToast("Reading résumé…", "info");
      try {
        const response = await fetch("/profile/parse-resume", {method:"POST",headers:{"X-CSRF-Token":csrfToken},body});
        const data = await readJsonResponse(response, "Could not read résumé.", "E3313");
        if (!response.ok) throw new Error(data.message || "Could not read résumé.");
        suggestions = data.suggestions; resumeContent.replaceChildren();
        const fields = [["Name", "name", [suggestions.first_name, suggestions.last_name].filter(Boolean).join(" ")], ["Home City, State", "location", suggestions.home_location], ["Skills", "skills", (suggestions.skills || []).join(", ")], ["Work history", "history", (suggestions.work_history || []).map(j => `${j.role} · ${j.company} · ${j.dates}`).join("\n")]];
        fields.forEach(([label, key, value]) => {
          const row = document.createElement("label"); row.className = "resume-suggestion";
          const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.dataset.field = key; checkbox.checked = Boolean(value); checkbox.disabled = !value;
          const text = document.createElement("span"); text.textContent = `${label}: ${value || "Not found"}`; row.append(checkbox, text); resumeContent.append(row);
        }); resumeDialog.showModal();
      } catch (error) { showToast(error.message, "danger"); } finally { resumeFile.value = ""; }
    });
    $("#apply-resume-review").addEventListener("click", () => {
      if (!suggestions) return;
      const selected = new Set($$("input:checked", resumeContent).map(el => el.dataset.field));
      if (selected.has("name")) { if (!$("#first-name").value) $("#first-name").value = suggestions.first_name || ""; if (!$("#last-name").value) $("#last-name").value = suggestions.last_name || ""; }
      if (selected.has("location") && !$("#home-location").value) { $("#home-location").value = suggestions.home_location || ""; $("#home-location").dispatchEvent(new Event("input")); }
      if (selected.has("skills")) { (suggestions.skills || []).forEach(addSkill); showRelatedSkills(); }
      if (selected.has("history")) { (suggestions.work_history || []).forEach(job => { if (!history.some(existing => existing.role.toLowerCase() === job.role.toLowerCase() && existing.company.toLowerCase() === job.company.toLowerCase())) history.push(job); }); renderHistory(); }
      resumeDialog.close(); showToast("Suggestions added for review. Save Profile to keep them.", "success");
    });
    const avatarInput = $("#avatar-file"), cropDialog = $("#avatar-crop"), canvas = $("#avatar-canvas"), ctx = canvas.getContext("2d"), zoom = $("#avatar-zoom");
    let photo = null, offsetX = 0, offsetY = 0, dragging = false, pointerX = 0, pointerY = 0;
    $("#change-avatar").addEventListener("click", () => avatarInput.click());
    function drawCrop() { if (!photo) return; const base = Math.max(320/photo.width,320/photo.height), width = photo.width*base*Number(zoom.value), height = photo.height*base*Number(zoom.value); const x = Math.max(320-width,Math.min(0,(320-width)/2+offsetX)), y = Math.max(320-height,Math.min(0,(320-height)/2+offsetY)); ctx.clearRect(0,0,320,320); ctx.drawImage(photo,x,y,width,height); }
    avatarInput.addEventListener("change", () => { const file = avatarInput.files[0]; if (!file) return; if (!file.type.startsWith("image/") || file.size > 5*1024*1024) { showToast("Use an image under 5 MB.","danger"); avatarInput.value=""; return; } const url = URL.createObjectURL(file); photo = new Image(); photo.onload = () => { URL.revokeObjectURL(url); offsetX=0;offsetY=0;zoom.value="1";drawCrop();cropDialog.showModal(); }; photo.onerror = () => { URL.revokeObjectURL(url); showToast("Could not open image.","danger"); }; photo.src=url; });
    zoom.addEventListener("input",drawCrop);
    canvas.addEventListener("pointerdown",e=>{dragging=true;pointerX=e.clientX;pointerY=e.clientY;canvas.setPointerCapture(e.pointerId);});
    canvas.addEventListener("pointermove",e=>{if(!dragging)return;offsetX+=e.clientX-pointerX;offsetY+=e.clientY-pointerY;pointerX=e.clientX;pointerY=e.clientY;drawCrop();});
    canvas.addEventListener("pointerup",()=>dragging=false);
    $("#cancel-avatar").addEventListener("click",()=>cropDialog.close());
    $("#save-avatar").addEventListener("click",()=>{drawCrop();const value=canvas.toDataURL("image/jpeg",0.82);$("#avatar-data").value=value;const avatar=$("#change-avatar");avatar.replaceChildren();const img=document.createElement("img");img.src=value;img.alt="";avatar.append(img);cropDialog.close();showToast("Photo ready. Save Profile to keep it.","success");});
    const savedAvatar=$("#avatar-data").value;let submittingProfile=false;
    window.addEventListener("beforeunload",event=>{if(!submittingProfile&&$("#avatar-data").value!==savedAvatar){event.preventDefault();event.returnValue="";}});
    profileForm.addEventListener("submit",()=>{submittingProfile=true;});
  }
  $$(".save-history-title").forEach(button => button.addEventListener("click", async () => {
    button.disabled = true;
    try { const response = await fetch("/profile/save-title",{method:"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":csrfToken},body:JSON.stringify({title:button.dataset.jobTitle})}); const data=await readJsonResponse(response,"Could not save title."); if(!response.ok)throw new Error(data.message); button.textContent="Saved"; showToast("Job title saved to your profile.","success"); }
    catch(error){button.disabled=false;showToast(error.message,"danger");}
  }));
  const dashboardRefresh = $("#refresh-dashboard"), dashboardRefreshStatus = $("#dashboard-refresh-status");
  if(dashboardRefresh){
    const ids = $$(".saved-job-card").map(card=>Number(card.dataset.companyId)).filter(Number.isSafeInteger);
    dashboardRefresh.addEventListener("click", async () => {
      if(!ids.length){ dashboardRefreshStatus.textContent="No saved listings match these filters.";dashboardRefreshStatus.hidden=false;return; }
      dashboardRefresh.disabled=true;dashboardRefreshStatus.hidden=false;dashboardRefreshStatus.textContent=`Starting refresh for ${ids.length} saved listing${ids.length===1?"":"s"} shown below…`;
      try { const response=await fetch("/refresh-search",{method:"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":csrfToken},body:JSON.stringify({company_ids:ids})}); const data=await readJsonResponse(response,"Could not refresh listings.");if(!response.ok)throw new Error(data.message); if(data.status==="already_running")throw new Error("Another search or refresh is already running."); if(data.status==="no_results")throw new Error(data.message);const timer=setInterval(async()=>{try{const res=await fetch("/search-status",{cache:"no-store"});const status=await res.json();dashboardRefreshStatus.textContent=status.running?`${status.progress||"Checking saved listings"} · ${status.elapsed_seconds||0}s elapsed`:status.error||`Refresh complete for ${ids.length} saved listing${ids.length===1?"":"s"}. Reloading…`;if(!status.running){clearInterval(timer);dashboardRefresh.disabled=false;if(!status.error)location.reload();}}catch(error){clearInterval(timer);dashboardRefresh.disabled=false;dashboardRefreshStatus.textContent=error.message;}},2000);}
      catch(error){dashboardRefresh.disabled=false;dashboardRefreshStatus.textContent=error.message;}
    });
  }

  const startButton=$("#start-search"),refreshButton=$("#refresh-search"),searchingStatus=$("#searching-status"),activityLabel=$("#activity-label"),progressBar=$("#search-progress-bar");
  if(startButton&&refreshButton&&searchingStatus&&activityLabel){let wasRunning=document.body.dataset.scraperRunning==="true",currentMode=document.body.dataset.scraperMode||null;
    function getSearchCriteria(){showFormError("");searchCityEditor.addPending?.();const jobTitle=normalizeJobTitles(jobTitleInput?.value||"");const cities=searchCityEditor.getItems();if(!jobTitle){showFormError("Enter at least one job title.");jobTitleInput?.focus();return null;}if(!cities.length){showFormError("Add a city and state or a full state name.");$("#search-city-input")?.focus();return null;}const states=[...new Set(cities.map((i)=>i.scope==="state"?i.city:(i.city.includes(",")?i.city.split(",").pop().trim():"")).filter(Boolean))];const state=states.join(", ")||stateInput?.value.trim()||"United States";if(stateInput)stateInput.value=state;localStorage.setItem("jobFinderJobTitle",jobTitle);localStorage.setItem("jobFinderState",state);return{job_title:jobTitle,state,cities};}
    function setActivity(running,mode=null){startButton.disabled=running;refreshButton.disabled=running;searchingStatus.hidden=true;if(progressBar)progressBar.hidden=true;activityLabel.textContent=mode==="import"?"Importing captured jobs":mode==="update"?"Updating existing results":mode==="refresh"?"Refreshing results":mode==="replacement"?"Finding a replacement":"Searching";if(running)showLoading(activityLabel.textContent+"…",mode==="import"?"Checking captured jobs against your profile's job titles and cities.":(mode==="update"||mode==="refresh")?"Checking existing results for current information.":"Job Finder is collecting and validating results.",true);else hideLoading();}
    function showStopping(){showLoading("Stopping…","Finishing the current page and shutting down the search engine. This can take up to a minute.",false);}
    async function stopCurrent(){let data={};try{const response=await fetch("/stop-search",{method:"POST",headers:{"X-CSRF-Token":csrfToken}});data=await response.json();}catch{}if(data.status==="stopping"){showStopping();return;}setActivity(false);wasRunning=false;currentMode=null;try{sessionStorage.setItem("jobFinderToast",JSON.stringify({message:"Stopped. Showing what was found so far.",type:"info"}));}catch{}location.reload();}
    if(loadingStop)loadingStop.addEventListener("click",stopCurrent);
    async function checkSearchStatus(){try{const response=await fetchWithTimeout("/search-status",{cache:"no-store"},8000);const data=await readJsonResponse(response,"Could not read Job Finder status.","E1301");if(!response.ok)throw new Error(serverMessage(data,"Could not read Job Finder status.","E1301"));const previousMode=currentMode;currentMode=data.mode||null;if(data.running&&data.stopping){showStopping();wasRunning=true;return;}if(wasRunning||data.running)setActivity(data.running,currentMode);if(data.running&&loadingDetail){const seconds=data.elapsed_seconds||0;const elapsed=`${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,"0")}`;loadingDetail.replaceChildren();
      const progressLine=document.createElement("span");progressLine.className="activity-detail-line";progressLine.textContent=data.progress||"Preparing search";loadingDetail.appendChild(progressLine);
      if((currentMode==="search"||currentMode==="replacement")&&data.checked!==null){const checkedLine=document.createElement("span");checkedLine.className="activity-detail-line";checkedLine.textContent=`Checked: ${data.checked}`;loadingDetail.appendChild(checkedLine);}
      if((currentMode==="search"||currentMode==="replacement")&&data.passed!==null){const passedLine=document.createElement("span");passedLine.className="activity-detail-line";const already=Math.max(0,(data.passed||0)-(data.saved||0));passedLine.textContent=`New: ${data.saved||0}${data.limit?` of ${data.limit}`:""} · Already in your list: ${already}`;loadingDetail.appendChild(passedLine);}
      const elapsedLine=document.createElement("span");elapsedLine.className="activity-detail-line";elapsedLine.textContent=`${elapsed} elapsed`;loadingDetail.appendChild(elapsedLine);}if(wasRunning&&!data.running){if(data.error)sessionStorage.setItem("jobFinderToast",JSON.stringify({message:data.error,type:"danger"}));else sessionStorage.setItem("jobFinderToast",JSON.stringify({message:previousMode==="import"?(data.stop_reason||"Import complete"):previousMode==="update"?"Update complete":previousMode==="refresh"?"Refresh complete":previousMode==="replacement"?"Replacement search finished":(data.stop_reason==="Stopped by user"?"Stopped. Showing what was found so far.":data.stop_reason)||"Search complete",type:"success"}));location.reload();return;}wasRunning=data.running;}catch(error){console.error(error);}}
    requestReplacement=async()=>{
      try {
        const response=await fetchWithTimeout("/replace-result",{method:"POST",headers:{"X-CSRF-Token":csrfToken}},12000);
        const data=await readJsonResponse(response,"Could not start a replacement search.","E2110");
        if(!response.ok)throw new Error(serverMessage(data,"Could not start a replacement search.","E2110"));
        if(data.status==="no_results") { showToast(data.message,"info");return; }
        if(data.status==="already_running") { showToast("A search is already running. Try again after it finishes.","info");return; }
        wasRunning=true;currentMode="replacement";setActivity(true,"replacement");
        showToast("Searching for a replacement listing…","info");
      } catch(error) { showToast(error.message||"Could not start replacement search.","danger"); }
    };
    async function startAction(url,mode,payload=null){
      setActivity(true,mode);
      currentMode=mode;
      try{
        const options={method:"POST",headers:{"X-CSRF-Token":csrfToken}};
        if(payload){options.headers={"Content-Type":"application/json","X-CSRF-Token":csrfToken};options.body=JSON.stringify(payload);}
        const response=await fetchWithTimeout(url,options,12000);
        const contentType=response.headers.get("content-type")||"";
        if(!contentType.includes("application/json"))throw new Error(codedMessage("E1003",`Unexpected dashboard response from ${url} (HTTP ${response.status}).`));
        const data=await response.json();
        if(!response.ok)throw new Error(serverMessage(data,"The action could not be started.","E2000"));
        if(data.status==="no_results"){
          setActivity(false);wasRunning=false;currentMode=null;
          showStatusModal("Nothing to update",data.message||"There are no existing results to update.","info");
          return;
        }
        wasRunning=true;
      }catch(error){
        setActivity(false);wasRunning=false;currentMode=null;
        showFormError(error?.name==="AbortError"?"Could not retrieve information. The request timed out. Please try again.":error.message);
      }
    }
    startButton.addEventListener("click",()=>{const c=getSearchCriteria();if(c)startAction("/start-search","search",c);});refreshButton.addEventListener("click",()=>{
      const companyIds=$$(".result-row").filter(row=>!row.hidden).map(row=>Number(row.dataset.companyId)).filter(Number.isSafeInteger);
      startAction("/refresh-search","refresh",{company_ids:companyIds});
    });setActivity(wasRunning,currentMode);setInterval(checkSearchStatus,2000);
  }

  // Job title type-ahead: any input with data-title-suggest="single" or "list" (comma-separated titles).
  function attachTitleSuggest(input) {
    const multi = input.dataset.titleSuggest === "list";
    const box = document.createElement("ul");
    box.className = "title-suggest"; box.id = `${input.id}-suggest`; box.hidden = true; box.setAttribute("role", "listbox");
    input.setAttribute("autocomplete", "off"); input.setAttribute("aria-autocomplete", "list"); input.setAttribute("aria-controls", box.id);
    input.insertAdjacentElement("afterend", box);
    input.parentElement.classList.add("title-suggest-host");
    let items = [], active = -1, timer = null, asked = "";
    const term = () => (multi ? input.value.split(",").pop() : input.value).trim();
    function render() {
      box.replaceChildren(...items.map((title, i) => {
        const li = document.createElement("li");
        li.id = `${box.id}-${i}`; li.setAttribute("role", "option"); li.textContent = title;
        if (i === active) { li.classList.add("active"); li.setAttribute("aria-selected", "true"); }
        li.addEventListener("mousedown", (event) => { event.preventDefault(); choose(title); });
        return li;
      }));
      box.hidden = !items.length;
      input.setAttribute("aria-expanded", String(!box.hidden));
      if (active >= 0) input.setAttribute("aria-activedescendant", `${box.id}-${active}`); else input.removeAttribute("aria-activedescendant");
    }
    function choose(title) {
      if (multi) {
        const parts = input.value.split(",").map((part) => part.trim()); parts[parts.length - 1] = title;
        input.value = `${parts.filter(Boolean).join(", ")}, `;
      } else input.value = title;
      items = []; active = -1; render();
      input.dispatchEvent(new Event("input", { bubbles: true })); input.dispatchEvent(new Event("change", { bubbles: true }));
    }
    async function load() {
      const typed = term();
      if (typed.length < 2) { items = []; render(); return; }
      if (typed === asked) return;
      asked = typed;
      try {
        const response = await fetch(`/job-title-matches?q=${encodeURIComponent(typed)}`, { cache: "no-store" });
        const data = await response.json();
        if (term() !== typed) return;
        const have = multi ? input.value.split(",").map((t) => t.trim().toLowerCase()) : [];
        items = (data.matches || []).filter((title) => title.toLowerCase() === typed.toLowerCase() ? false : !have.slice(0, -1).includes(title.toLowerCase()));
        active = -1; render();
      } catch { items = []; render(); }
    }
    input.addEventListener("input", () => { clearTimeout(timer); asked = ""; timer = setTimeout(load, 150); });
    input.addEventListener("keydown", (event) => {
      if (box.hidden) return;
      if (event.key === "ArrowDown") { event.preventDefault(); active = (active + 1) % items.length; render(); }
      else if (event.key === "ArrowUp") { event.preventDefault(); active = (active - 1 + items.length) % items.length; render(); }
      else if (event.key === "Enter" && active >= 0) { event.preventDefault(); choose(items[active]); }
      else if (event.key === "Escape") { items = []; render(); }
    });
    input.addEventListener("blur", () => setTimeout(() => { items = []; render(); }, 120));
  }
  $$("input[data-title-suggest]").forEach(attachTitleSuggest);

  // Collapsible panels marked data-remember start closed and keep the user's choice.
  $$("details[data-remember]").forEach((panel) => {
    const key = panel.dataset.remember;
    try { panel.open = localStorage.getItem(key) === "1"; } catch {}
    panel.addEventListener("toggle", () => { try { localStorage.setItem(key, panel.open ? "1" : "0"); } catch {} });
  });

  // Saved jobs start collapsed. #job-ID (added after saving a card's status or notes) reopens that card.
  if (page === "dashboard") {
    const cards = $$(".saved-job-collapse");
    const setAll = (open) => cards.forEach((card) => { card.open = open; });
    document.getElementById("expand-all-jobs")?.addEventListener("click", () => setAll(true));
    document.getElementById("collapse-all-jobs")?.addEventListener("click", () => setAll(false));
    const target = /^#job-\d+$/.test(location.hash) ? document.querySelector(`${location.hash} .saved-job-collapse`) : null;
    if (target) { target.open = true; target.closest(".saved-job-card").scrollIntoView({ block: "start" }); }

    // Recent Searches starts collapsed; remember if the user leaves it open.
    const history = document.getElementById("recent-searches");
    if (history) {
      try { history.open = localStorage.getItem("jobFinder.recentSearchesOpen") === "1"; } catch {}
      history.addEventListener("toggle", () => {
        try { localStorage.setItem("jobFinder.recentSearchesOpen", history.open ? "1" : "0"); } catch {}
      });
    }
  }
});
