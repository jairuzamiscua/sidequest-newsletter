@app.route('/events', methods=['GET'])
def events_overview_page():
    """Public events overview page – tournaments, birthdays, and a public calendar"""
    events_html = '''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1.0"/>
  <title>Events & Tournaments – SideQuest Canterbury</title>
  <style>
    *{margin:0;padding:0;box-sizing:border-box}
    :root{
      --primary:#FFD700;
      --accent:#FF6B35;
      --dark:#0a0a0a;
      --dark-2:#141414;
      --text:#ffffff;
      --muted:#9a9a9a;
      --border:rgba(255,255,255,.06);
      --special:#8B5FBF;
    }
    body{font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:var(--dark);color:var(--text);line-height:1.6;overflow-x:hidden;cursor:none}

    /* Opening reveal animation */
    .reveal-overlay{position:fixed;inset:0;background:var(--dark);z-index:9999;display:flex;align-items:center;justify-content:center;flex-direction:column;transition:opacity 1s ease,visibility 1s ease}
    .reveal-overlay.hide{opacity:0;visibility:hidden}
    .reveal-logo{width:400px;height:auto;opacity:0;transform:scale(0.8);animation:logoReveal 2s ease-out forwards}
    .reveal-tagline{font-size:1.2rem;color:var(--muted);margin-top:20px;opacity:0;animation:fadeInUp 1s ease-out 1.5s forwards}
    .reveal-loader{margin-top:30px;width:200px;height:2px;background:rgba(255,255,255,.1);border-radius:1px;overflow:hidden;opacity:0;animation:fadeIn 0.5s ease 2s forwards}
    .reveal-progress{height:100%;background:linear-gradient(90deg,var(--primary),var(--accent));width:0;animation:loadProgress 1.5s ease-out 2.2s forwards}

    @keyframes logoReveal{
      0%{opacity:0;transform:scale(0.8) translateY(30px)}
      50%{opacity:1;transform:scale(1.05) translateY(-10px)}
      100%{opacity:1;transform:scale(1) translateY(0)}
    }
    @keyframes fadeInUp{
      from{opacity:0;transform:translateY(20px)}
      to{opacity:1;transform:translateY(0)}
    }
    @keyframes fadeIn{
      from{opacity:0}
      to{opacity:1}
    }
    @keyframes loadProgress{
      from{width:0}
      to{width:100%}
    }

    /* Page content initially hidden */
    .page-content{opacity:0;transform:translateY(30px);transition:all 1s ease}
    .page-content.revealed{opacity:1;transform:translateY(0)}

    /* Staggered reveal animations for page elements */
    .reveal-element{opacity:0;transform:translateY(30px);transition:all 0.8s ease}
    .reveal-element.animate{opacity:1;transform:translateY(0)}
    .reveal-element:nth-child(1){transition-delay:0.1s}
    .reveal-element:nth-child(2){transition-delay:0.2s}
    .reveal-element:nth-child(3){transition-delay:0.3s}
    .reveal-element:nth-child(4){transition-delay:0.4s}

    /* Enhanced stats animation */
    .stat{opacity:0;transform:translateY(30px);transition:all 0.6s ease}
    .stat.animate{opacity:1;transform:translateY(0)}
    .stat:nth-child(1){transition-delay:0.2s}
    .stat:nth-child(2){transition-delay:0.3s}
    .stat:nth-child(3){transition-delay:0.4s}
    .stat:nth-child(4){transition-delay:0.5s}

    /* Reduced motion respect */
    @media (prefers-reduced-motion: reduce){
      *{animation:none!important;transition:none!important}
      .reveal-overlay{display:none}
      .page-content,.reveal-element,.stat{opacity:1!important;transform:none!important}
    }

    /* Cursor */
    .cursor{width:20px;height:20px;border:2px solid var(--primary);border-radius:50%;position:fixed;pointer-events:none;transition:all .1s ease;z-index:9999;mix-blend-mode:difference}
    .cursor-f{width:40px;height:40px;background:rgba(255,215,0,.1);border-radius:50%;position:fixed;pointer-events:none;transition:all .3s ease;z-index:9998}
    .cursor.active{transform:scale(.5);background:var(--primary)}

    /* Noise overlay */
    body::before{content:'';position:fixed;inset:0;background:url('data:image/svg+xml,%3Csvg viewBox="0 0 256 256" xmlns="http://www.w3.org/2000/svg"%3E%3Cfilter id="n"%3E%3CfeTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="4"/%3E%3C/filter%3E%3Crect width="100%25" height="100%25" filter="url(%23n)" opacity="0.03"/%3E%3C/svg%3E');pointer-events:none;z-index:1}

    /* Hero */
    .hero{min-height:92vh;display:flex;align-items:center;justify-content:center;position:relative;overflow:hidden;background:radial-gradient(ellipse at center,rgba(255,215,0,.05) 0%,transparent 70%)}
    .hero-bg{position:absolute;inset:0;overflow:hidden}
    .hero-bg::before{content:'';position:absolute;width:200%;height:200%;top:-50%;left:-50%;background:conic-gradient(from 0deg at 50% 50%,var(--primary) 0deg,transparent 60deg,transparent 300deg,var(--accent) 360deg);animation:spin 30s linear infinite;opacity:.1}
    @keyframes spin{100%{transform:rotate(360deg)}}
    .floating-shapes{position:absolute;inset:0}
    .shape{position:absolute;border:1px solid rgba(255,215,0,0.2);animation:float 20s infinite ease-in-out}
    .shape:nth-child(1){width:300px;height:300px;top:10%;left:10%;border-radius:30% 70% 70% 30%/30% 30% 70% 70%}
    .shape:nth-child(2){width:220px;height:220px;top:60%;right:10%;border-radius:63% 37% 54% 46%/55% 48% 52% 45%}
    .shape:nth-child(3){width:160px;height:160px;bottom:10%;left:30%;border-radius:40% 60% 60% 40%/60% 30% 70% 40%}
    @keyframes float{0%,100%{transform:translate(0,0) rotate(0) scale(1)}33%{transform:translate(30px,-30px) rotate(120deg) scale(1.1)}66%{transform:translate(-20px,20px) rotate(240deg) scale(.9)}}

    .hero-content{position:relative;z-index:10;text-align:center;padding:0 20px}
    .title{font-size:clamp(3rem,9vw,7rem);font-weight:900;letter-spacing:-.03em;line-height:.9;background:linear-gradient(135deg,var(--primary),var(--accent));-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:16px}
    .subtitle{color:var(--muted);max-width:760px;margin:0 auto}
    .stats{display:flex;gap:42px;justify-content:center;margin-top:42px;flex-wrap:wrap}
    .stat{text-align:center}
    .stat .num{font-size:2.6rem;font-weight:900;background:linear-gradient(135deg,var(--primary),var(--accent));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
    .stat .lbl{font-size:.9rem;color:var(--muted);text-transform:uppercase;letter-spacing:.12em;margin-top:8px}

    .scroll{position:absolute;bottom:36px;left:50%;transform:translateX(-50%);animation:bounce 2s infinite}
    .scroll::before{content:'';display:block;width:20px;height:30px;border:2px solid var(--primary);border-radius:15px}
    .scroll::after{content:'';display:block;width:4px;height:8px;background:var(--primary);border-radius:2px;position:absolute;top:8px;left:50%;transform:translateX(-50%);animation:scroll 2s infinite}
    @keyframes bounce{0%,100%{transform:translateX(-50%) translateY(0)}50%{transform:translateX(-50%) translateY(10px)}}
    @keyframes scroll{0%{opacity:0;transform:translateX(-50%) translateY(0)}50%{opacity:1}100%{opacity:0;transform:translateX(-50%) translateY(10px)}}

    /* Main */
    .wrap{max-width:1400px;margin:0 auto;padding:90px 20px}

    /* Tabs */
    .tabs{display:flex;justify-content:center;gap:8px;margin-bottom:64px;position:relative;flex-wrap:wrap}
    .tabs::before{content:'';position:absolute;bottom:-8px;left:50%;transform:translateX(-50%);width:100%;max-width:700px;height:1px;background:linear-gradient(90deg,transparent,var(--muted),transparent);opacity:.2}
    .tab{background:rgba(255,255,255,.02);border:1px solid var(--border);border-radius:999px;color:var(--muted);font-weight:800;text-transform:uppercase;letter-spacing:.06em;padding:12px 20px;cursor:pointer;transition:all .2s ease}
    .tab[aria-selected="true"]{color:#141414;background:linear-gradient(135deg,var(--primary),var(--accent));border-color:transparent}
    .tab:focus-visible{outline:3px solid var(--primary)}
    .panel{display:none}
    .panel.active{display:block}

    /* Special pill styles */
    .special{background:rgba(139,95,191,.18);color:#b68dd8}

    /* Sections */
    .section-head{text-align:center;margin-bottom:44px}
    .section-title{font-size:clamp(2.4rem,5vw,3.8rem);font-weight:900;letter-spacing:-.02em;margin-bottom:10px;display:inline-block;position:relative}
    .section-title::after{content:'';position:absolute;bottom:-10px;left:50%;transform:translateX(-50%);width:64px;height:4px;background:linear-gradient(90deg,var(--primary),var(--accent));border-radius:2px}
    .section-sub{color:var(--muted);max-width:680px;margin:0 auto}

    /* Cards */
    .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(380px,1fr));gap:26px}
    .card{background:var(--dark-2);border:1px solid var(--border);border-radius:18px;overflow:hidden;transition:transform .25s ease,border-color .25s ease;position:relative}
    .card::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,transparent,var(--primary),transparent);opacity:0;transition:opacity .3s ease}
    .card:hover{transform:translateY(-6px);border-color:rgba(255,215,0,.25);box-shadow:0 20px 40px rgba(255,215,0,.08)}
    .card:hover::before{opacity:1}
    .banner{position:relative;height:210px;background:#111 center/cover no-repeat}
    .banner::after{content:'';position:absolute;inset:0;background:linear-gradient(to top,rgba(0,0,0,.45),transparent 60%)}
    .body{padding:22px}
    .pill{display:inline-block;padding:6px 12px;border-radius:999px;font-size:.75rem;font-weight:900;letter-spacing:.06em;margin-bottom:12px}
    .ok{background:rgba(255,215,0,.18);color:#ffd86a}
    .warn{background:rgba(255,107,53,.18);color:#ff9a78}
    .soon{background:rgba(255,215,0,.18);color:#ffd86a}
    .name{font-size:1.45rem;font-weight:850;margin-bottom:6px}
    .sub{color:#ff8d6a;font-weight:700;margin-bottom:16px}
    .meta{display:grid;grid-template-columns:repeat(2,1fr);gap:12px 16px;margin-bottom:18px}
    .meta-item{display:flex;align-items:center;gap:8px;color:var(--muted);white-space:nowrap}
    .btn{width:100%;padding:15px;background:linear-gradient(135deg,var(--primary),var(--accent));color:#141414;border:none;border-radius:12px;font-weight:800;text-transform:uppercase;letter-spacing:.05em;cursor:pointer;transition:all .2s ease;font-size:.9rem;position:relative;overflow:hidden}
    .btn::before{content:'';position:absolute;top:0;left:-100%;width:100%;height:100%;background:linear-gradient(90deg,transparent,rgba(255,255,255,.2),transparent);transition:left .6s ease}
    .btn:hover{transform:translateY(-2px);box-shadow:0 8px 24px rgba(255,215,0,.3)}
    .btn:hover::before{left:100%}
    .btn:active{transform:translateY(0);box-shadow:0 4px 12px rgba(255,215,0,.2)}
    .btn:disabled{opacity:.6;cursor:not-allowed;transform:none}
    .btn:disabled:hover{box-shadow:none}
    .btn:disabled::before{display:none}

    /* Calendar - Vertical card layout like Special Events */
    .cal-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(380px,1fr));gap:26px}
    .cal-item{background:var(--dark-2);border:1px solid var(--border);border-radius:18px;overflow:hidden;transition:transform .25s ease,border-color .25s ease;position:relative}
    .cal-item::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,transparent,var(--primary),transparent);opacity:0;transition:opacity .3s ease}
    .cal-item:hover{transform:translateY(-6px);border-color:rgba(255,215,0,.25);box-shadow:0 20px 40px rgba(255,215,0,.08)}
    .cal-item:hover::before{opacity:1}
    
    .cal-banner{position:relative;height:210px;background:#111 center/cover no-repeat}
    .cal-banner::after{content:'';position:absolute;inset:0;background:linear-gradient(to top,rgba(0,0,0,.45),transparent 60%)}
    .cal-date-overlay{position:absolute;top:16px;left:16px;background:linear-gradient(135deg,var(--primary),var(--accent));color:#141414;padding:12px 16px;border-radius:12px;display:flex;flex-direction:column;align-items:center;min-width:70px;z-index:2}
    .cal-date-overlay .month{font-size:.7rem;font-weight:900;letter-spacing:.15em;text-transform:uppercase;opacity:.9;line-height:1}
    .cal-date-overlay .day{font-size:1.8rem;line-height:.9;font-weight:900;margin-top:2px}
    
    .cal-body{padding:22px}
    .cal-type-pill{display:inline-block;padding:6px 12px;border-radius:999px;font-size:.75rem;font-weight:900;letter-spacing:.06em;margin-bottom:12px}
    .cal-type-pill.tournament{background:rgba(255,215,0,.18);color:#ffd86a}
    .cal-type-pill.games_night{background:rgba(255,215,0,.18);color:#ffd86a}
    .cal-type-pill.special{background:rgba(139,95,191,.18);color:#b68dd8}
    .cal-title{font-size:1.45rem;font-weight:850;margin-bottom:6px;line-height:1.2}
    .cal-subtitle{color:#ff8d6a;font-weight:700;margin-bottom:16px}
    .cal-meta{display:grid;grid-template-columns:repeat(2,1fr);gap:12px 16px;margin-bottom:18px}
    .cal-meta-item{display:flex;align-items:center;gap:8px;color:var(--muted);white-space:nowrap}
    .cal-description{color:var(--muted);margin-bottom:18px;font-size:.95rem;line-height:1.4}
    .cal-btn{width:100%;padding:15px;background:linear-gradient(135deg,var(--primary),var(--accent));color:#141414;border:none;border-radius:12px;font-weight:800;text-transform:uppercase;letter-spacing:.05em;cursor:pointer;transition:all .2s ease;font-size:.9rem;position:relative;overflow:hidden;text-decoration:none;display:flex;align-items:center;justify-content:center}
    .cal-btn::before{content:'';position:absolute;top:0;left:-100%;width:100%;height:100%;background:linear-gradient(90deg,transparent,rgba(255,255,255,.2),transparent);transition:left .6s ease}
    .cal-btn:hover{transform:translateY(-2px);box-shadow:0 8px 24px rgba(255,215,0,.3)}
    .cal-btn:hover::before{left:100%}

    /* Loading / helpers */
    .loading{grid-column:1/-1;text-align:center;padding:60px;color:var(--muted)}
    .spin{width:48px;height:48px;border:3px solid rgba(255,215,0,.12);border-top-color:var(--primary);border-radius:50%;animation:spin 1s linear infinite;margin:0 auto 16px}

    /* Quick actions */
    .quick{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:26px;margin-top:64px}
    .q-card{background:var(--dark-2);border:1px solid var(--border);border-radius:18px;padding:36px;text-align:center;transition:all .3s ease;cursor:pointer}
    .q-card:hover{transform:translateY(-6px);border-color:rgba(255,215,0,.3);box-shadow:0 20px 40px rgba(255,215,0,.08)}
    .q-title{font-size:1.1rem;font-weight:900;color:var(--primary);margin-bottom:10px}
    .q-text{color:var(--muted);margin-bottom:18px}
    .q-btn{padding:12px 24px;background:linear-gradient(135deg,var(--primary),var(--accent));color:#141414;border:none;border-radius:10px;font-weight:800;cursor:pointer;font-size:.85rem;text-transform:uppercase;letter-spacing:.04em;transition:all .2s ease;position:relative;overflow:hidden}
    .q-btn::before{content:'';position:absolute;top:0;left:-100%;width:100%;height:100%;background:linear-gradient(90deg,transparent,rgba(255,255,255,.2),transparent);transition:left .6s ease}
    .q-btn:hover{transform:translateY(-1px);box-shadow:0 6px 16px rgba(255,215,0,.25)}
    .q-btn:hover::before{left:100%}

    @media (max-width:768px){
      body{cursor:auto}
      .cursor,.cursor-f{display:none}
      .grid{grid-template-columns:1fr;gap:16px}
      .stats{gap:16px;flex-wrap:wrap}
      .stat .num{font-size:2.2rem}
      .hero-content{padding:0 16px}
      .wrap{padding:60px 16px}
      .tabs{gap:6px;padding:0 8px}
      .tab{padding:10px 16px;font-size:.8rem}
      .section-head{margin-bottom:32px}
      .quick{gap:16px}
      .q-card{padding:24px}
      .cal-grid{grid-template-columns:1fr;gap:16px}
      .cal-banner{height:180px}
      .cal-date-overlay{top:12px;left:12px;padding:10px 12px;min-width:60px}
      .cal-date-overlay .month{font-size:.65rem}
      .cal-date-overlay .day{font-size:1.5rem}
      .cal-body{padding:18px}
      .cal-title{font-size:1.25rem}
      .cal-meta{grid-template-columns:1fr;gap:8px}
      .cal-meta-item{font-size:.85rem}
    }
  </style>
</head>
<body>
  <!-- Opening Reveal Overlay -->
  <div class="reveal-overlay" id="revealOverlay">
    <img src="/static/brand/sidequest-logo.png" alt="SideQuest" class="reveal-logo" />
    <div class="reveal-tagline">Canterbury's Premier Gaming Hub</div>
    <div class="reveal-loader">
      <div class="reveal-progress"></div>
    </div>
  </div>

  <!-- Page Content -->
  <div class="page-content" id="pageContent">
    <div class="cursor"></div><div class="cursor-f"></div>

    <!-- Hero -->
    <section class="hero reveal-element" aria-label="Events hero">
      <div class="hero-bg">
        <div class="floating-shapes"><div class="shape"></div><div class="shape"></div><div class="shape"></div></div>
      </div>
      <div class="hero-content">
        <h1 class="title reveal-element">LEVEL UP YOUR GAME</h1>
        <p class="subtitle reveal-element">Elite tournaments, relaxed game nights, unforgettable birthdays & special events — all in one sleek hub.</p>
        <div class="stats reveal-element" role="group" aria-label="Live counters">
          <div class="stat"><div class="num" id="upcomingCount">0</div><div class="lbl">Public Events</div></div>
          <div class="stat"><div class="num" id="tournamentCount">0</div><div class="lbl">Tournaments</div></div>
          <div class="stat"><div class="num" id="gamesNightCount">0</div><div class="lbl">Games Nights</div></div>
          <div class="stat"><div class="num" id="specialEventCount">0</div><div class="lbl">Special Events</div></div>
        </div>
      </div>
      <div class="scroll" aria-hidden="true"></div>
    </section>

    <!-- Main -->
    <main class="wrap reveal-element">
      <!-- Tabs -->
      <div class="tabs reveal-element" role="tablist" aria-label="Events navigation">
        <button class="tab" role="tab" aria-selected="true" id="tab-tournaments" aria-controls="panel-tournaments">Tournaments</button>
        <button class="tab" role="tab" aria-selected="false" id="tab-games" aria-controls="panel-games">Games Nights</button>
        <button class="tab" role="tab" aria-selected="false" id="tab-special" aria-controls="panel-special">Special Events</button>
        <button class="tab" role="tab" aria-selected="false" id="tab-birthdays" aria-controls="panel-birthdays">Birthday Parties</button>
        <button class="tab" role="tab" aria-selected="false" id="tab-calendar" aria-controls="panel-calendar">Calendar</button>
      </div>

      <!-- Tournaments -->
      <section id="panel-tournaments" class="panel active" role="tabpanel" aria-labelledby="tab-tournaments">
        <div class="section-head">
          <h2 class="section-title">Tournament Arena</h2>
          <p class="section-sub">Compete in polished, high-stakes brackets. Real prizes. Pro vibes.</p>
        </div>
        <div id="tournaments-grid" class="grid">
          <div class="loading"><div class="spin"></div>Loading tournaments…</div>
        </div>
      </section>

      <!-- Games Nights -->
      <section id="panel-games" class="panel" role="tabpanel" aria-labelledby="tab-games">
        <div class="section-head">
          <h2 class="section-title">Games Night</h2>
          <p class="section-sub">Casual sessions, open tables, great atmosphere. Bring friends or meet new ones.</p>
        </div>
        <div id="games-grid" class="grid">
          <div class="loading"><div class="spin"></div>Loading games nights…</div>
        </div>
      </section>

      <!-- Special Events -->
      <section id="panel-special" class="panel" role="tabpanel" aria-labelledby="tab-special">
        <div class="section-head">
          <h2 class="section-title">Special Events</h2>
          <p class="section-sub">Unique experiences, themed nights, and exclusive gatherings. Don't miss out.</p>
        </div>
        <div id="special-grid" class="grid">
          <div class="loading"><div class="spin"></div>Loading special events…</div>
        </div>
      </section>

      <!-- Birthdays -->
      <section id="panel-birthdays" class="panel" role="tabpanel" aria-labelledby="tab-birthdays">
        <div class="section-head">
          <h2 class="section-title">Birthday Experiences</h2>
          <p class="section-sub">Two packages. Same premium energy. Pick your playstyle.</p>
        </div>
        <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(360px,1fr))">
          <article class="card">
            <div class="banner lazy-banner" data-src="/static/games/party-consoles.jpg"></div>
            <div class="body">
              <span class="pill ok">Available</span>
              <div class="name">Console Ultimate</div>
              <div class="sub">Premium birthday session</div>
              <div class="meta">
                <div class="meta-item" title="Players"><span class="i users"></span>Up to 12 players</div>
                <div class="meta-item" title="Perks"><span class="i check"></span>Decorations + gift pack</div>
              </div>
              <button class="btn" onclick="location.href='/birthday-booking'">Reserve Package</button>
            </div>
          </article>

          <article class="card">
            <div class="banner lazy-banner" data-src="/static/games/flex-gaming.jpg"></div>
            <div class="body">
              <span class="pill ok">Available</span>
              <div class="name">Flex Gaming</div>
              <div class="sub">Pay &amp; Play access</div>
              <div class="meta">
                <div class="meta-item"><span class="i clock"></span>Flexible time</div>
                <div class="meta-item"><span class="i list"></span>Custom lineup</div>
              </div>
              <button class="btn" onclick="location.href='/birthday-booking'">Book Now</button>
            </div>
          </article>
        </div>
      </section>

      <!-- Calendar -->
      <section id="panel-calendar" class="panel" role="tabpanel" aria-labelledby="tab-calendar">
        <div class="section-head">
          <h2 class="section-title">Public Event Calendar</h2>
          <p class="section-sub">Upcoming tournaments, game nights & special events. Birthdays hidden for privacy.</p>
        </div>
        <div id="cal-grid" class="cal-grid">
          <div class="loading"><div class="spin"></div>Loading calendar…</div>
        </div>

        <div class="quick">
          <div class="q-card" onclick="location.href='/signup'">
            <div class="q-title">Join Our Community</div>
            <div class="q-text">Get notified about new tournaments and game nights.</div>
            <button class="q-btn">Subscribe to Updates</button>
          </div>
          <div class="q-card" onclick="window.open('https://discord.gg/CuwQM7Zwuk','_blank')">
            <div class="q-title">Tournament Discord</div>
            <div class="q-text">Connect with players, teams, and admins in real time.</div>
            <button class="q-btn">Open Discord</button>
          </div>
          <div class="q-card" onclick="location.href='tel:012279915058'">
            <div class="q-title">Need Help?</div>
            <div class="q-text">Questions about events or bookings? We're here for you.</div>
            <button class="q-btn">01227 915058</button>
          </div>
        </div>
      </section>
    </main>
  </div>

  <script>
    /* ---------------- Opening Reveal Animation ---------------- */
    let revealComplete = false;
    
    function startRevealSequence(){
      const overlay = document.getElementById('revealOverlay');
      const pageContent = document.getElementById('pageContent');
      
      if(!overlay || !pageContent) {
        console.log('Reveal elements not found, skipping animation');
        return;
      }
      
      // Start hiding overlay after logo animation
      setTimeout(()=>{
        overlay.classList.add('hide');
        pageContent.classList.add('revealed');
        revealComplete = true;
        
        // Trigger page element animations
        setTimeout(()=>{
          document.querySelectorAll('.reveal-element').forEach((el,i)=>{
            setTimeout(()=>el.classList.add('animate'),i*100);
          });
          
          // Animate stats with delay
          setTimeout(()=>{
            document.querySelectorAll('.stat').forEach((el,i)=>{
              setTimeout(()=>el.classList.add('animate'),i*100);
            });
            // Start stats counting after stats animate in
            setTimeout(loadStats,400);
          },600);
        },500);
      },3800); // Total reveal time: logo(2s) + tagline(1s) + loader(1.5s) + buffer(0.3s)
    }

    // Skip animation on click/key during reveal
    document.addEventListener('click',()=>{
      if(!revealComplete) skipReveal();
    });
    document.addEventListener('keydown',(e)=>{
      if(!revealComplete && (e.key===' '||e.key==='Enter'||e.key==='Escape')) skipReveal();
    });
    
    function skipReveal(){
      if(revealComplete) return;
      revealComplete = true;
      const overlay = document.getElementById('revealOverlay');
      const pageContent = document.getElementById('pageContent');
      
      if(overlay) overlay.style.display = 'none';
      if(pageContent) pageContent.classList.add('revealed');
      document.querySelectorAll('.reveal-element,.stat').forEach(el=>el.classList.add('animate'));
      loadStats();
    }

    /* ---------------- Icons (inline SVG) ---------------- */
    const ICONS = {
      date:'<svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true"><rect x="3" y="4" width="18" height="18" rx="2" stroke="#9a9a9a" stroke-width="2"/><path d="M8 2v4M16 2v4M3 10h18" stroke="#9a9a9a" stroke-width="2" stroke-linecap="round"/></svg>',
      time:'<svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="12" cy="12" r="9" stroke="#9a9a9a" stroke-width="2"/><path d="M12 7v5l3 2" stroke="#9a9a9a" stroke-width="2" stroke-linecap="round"/></svg>',
      users:'<svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M16 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" stroke="#9a9a9a" stroke-width="2" stroke-linecap="round"/><circle cx="12" cy="7" r="3" stroke="#9a9a9a" stroke-width="2"/></svg>',
      fee:'<svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2" stroke="#9a9a9a" stroke-width="2"/><path d="M7 10h10M7 14h6" stroke="#9a9a9a" stroke-width="2" stroke-linecap="round"/></svg>',
      list:'<svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M4 7h16M4 12h10M4 17h7" stroke="#9a9a9a" stroke-width="2" stroke-linecap="round"/></svg>',
      check:'<svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="m20 6-11 11L4 12" stroke="#9a9a9a" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
      clock:'<svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="12" cy="12" r="9" stroke="#9a9a9a" stroke-width="2"/><path d="M12 7v5l3 2" stroke="#9a9a9a" stroke-width="2" stroke-linecap="round"/></svg>',
      tag:'<svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M20.59 13.41 12 22l-8-8 8-8 8.59 7.41Z" stroke="#9a9a9a" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
      star:'<svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2Z" stroke="#9a9a9a" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    };

    /* ---------------- Local-first game banners ---------------- */
    const GAME_IMAGES = {
      'valorant':'/static/games/valorant.jpg',
      'horror': '/static/games/horror.jpg',
      'cs2':'/static/games/cs2.jpg',
      'counter-strike 2':'/static/games/cs2.jpg',
      'league of legends':'/static/games/lol.jpg',
      'dota 2':'/static/games/dota2.jpg',
      'rocket league':'/static/games/rocket-league.jpg',
      'overwatch 2':'/static/games/overwatch2.jpg',
      'apex legends':'/static/games/apex.jpg',
      'rainbow six siege':'/static/games/r6.jpg',
      'minecraft':'/static/games/minecraft.jpg',
      'tekken 8':'/static/games/tekken8.jpg',
      'street fighter 6':'/static/games/sf6.jpg',
      'ea fc 24':'/static/games/eafc.jpg',
      'ea fc 25':'/static/games/eafc25.jpg',
      'f1':'/static/games/f1.jpg',
      'special':'/static/games/special-event.jpg',
      'themed':'/static/games/themed-night.jpg',
      'community':'/static/games/community.jpg',
      'cosplay':'/static/games/cosplay.jpg',
      'retro':'/static/games/retro.jpg',
      'generic':'/static/games/generic.jpg'
    };
    function bannerFor(title){
      if(!title) return GAME_IMAGES['generic'] || '/static/games/generic.jpg';
      const key = String(title).toLowerCase().trim();
      if(GAME_IMAGES[key]) return GAME_IMAGES[key];
      for(const k of Object.keys(GAME_IMAGES)){ if(key.includes(k)) return GAME_IMAGES[k]; }
      return GAME_IMAGES['generic'] || '/static/games/generic.jpg';
    }

    /* ---------------- Tabs (click + keyboard) ---------------- */
    const tabButtons = Array.from(document.querySelectorAll('.tab'));
    const panels = {
      tournaments: document.getElementById('panel-tournaments'),
      games: document.getElementById('panel-games'),
      special: document.getElementById('panel-special'),
      birthdays: document.getElementById('panel-birthdays'),
      calendar: document.getElementById('panel-calendar')
    };
    tabButtons.forEach(btn=>{
      btn.addEventListener('click', ()=>activateTab(btn));
      btn.addEventListener('keydown', e=>{
        const i = tabButtons.indexOf(btn);
        if(e.key==='ArrowRight') tabButtons[(i+1)%tabButtons.length].focus();
        if(e.key==='ArrowLeft') tabButtons[(i-1+tabButtons.length)%tabButtons.length].focus();
        if(e.key==='Enter' || e.key===' ') activateTab(btn);
      });
    });
    function activateTab(btn){
      tabButtons.forEach(b=>b.setAttribute('aria-selected','false'));
      Object.values(panels).forEach(p=>p.classList.remove('active'));
      btn.setAttribute('aria-selected','true');
      const id = btn.id.replace('tab-','');
      panels[id].classList.add('active');
      if(id==='tournaments') loadTournaments();
      if(id==='games') loadGamesNights();
      if(id==='special') loadSpecialEvents();
      if(id==='calendar') loadCalendar();
    }

    /* ---------------- Stats ---------------- */
    async function loadStats(){
      try{
        const r = await fetch('/api/events?upcoming=true',{credentials:'same-origin'});
        const j = await r.json();
        if(j.success){
          const publics = j.events.filter(e=>e.event_type!=='birthday');
          const tourns  = publics.filter(e=>e.event_type==='tournament');
          const games   = publics.filter(e=>e.event_type==='games_night' || /games?\s*night/i.test(e.title||''));
          const special = publics.filter(e=>e.event_type==='special');
          animate('#upcomingCount', publics.length);
          animate('#tournamentCount', tourns.length);
          animate('#gamesNightCount', games.length);
          animate('#specialEventCount', special.length);
        }
      }catch(e){ console.warn('stats err', e); }
    }
    function animate(sel, target){
      const el=document.querySelector(sel); const dur=900, steps=24, inc=(+target)/steps; let cur=0;
      const t=setInterval(()=>{cur+=inc; if(cur>=target){el.textContent=target;clearInterval(t)} else {el.textContent=Math.floor(cur)}}, dur/steps);
    }

    /* ---------------- Special Events loader ---------------- */
    async function loadSpecialEvents(){
      const grid = document.getElementById('special-grid');
      grid.innerHTML = '<div class="loading"><div class="spin"></div>Loading special events…</div>';
      try{
        const r = await fetch('/api/events?type=special&upcoming=true',{credentials:'same-origin'});
        const j = await r.json();
        if(j.success && j.events.length){
          grid.innerHTML = j.events.map(ev=>{
            const dt=new Date(ev.date_time), reg=ev.registration_count||0, cap=(ev.capacity||0)>0?ev.capacity:null;
            const spots = cap?Math.max(cap-reg,0):null;
            let pill='special', text='Join Event';
            if(spots!==null){ 
              if(spots===0){pill='warn'; text='Full'} 
              else if(spots<=3){pill='soon'; text=spots+' Spots Left'} 
              else {pill='special'; text='Available'}
            }
            const banner=bannerFor(ev.game_title||ev.title||'special');
            return cardHTML({banner, pillText:text, pillClass:pill, name:ev.title, sub:ev.game_title||'Special Event',
              dt, reg, cap, fee:ev.entry_fee>0?('£'+ev.entry_fee):'FREE', id:ev.id, description:ev.description});
          }).join('');
          lazyMountBanners();
        }else{
          grid.innerHTML = emptyState('No upcoming special events','New special events will be announced soon.');
        }
      }catch(e){
        grid.innerHTML = networkError('Couldn\\'t load special events. Please refresh.');
      }
    }

    /* ---------------- Tournaments loader ---------------- */
    async function loadTournaments(){
      const grid = document.getElementById('tournaments-grid');
      grid.innerHTML = '<div class="loading"><div class="spin"></div>Loading tournaments…</div>';
      try{
        const r = await fetch('/api/events?type=tournament&upcoming=true',{credentials:'same-origin'});
        const j = await r.json();
        if(j.success && j.events.length){
          grid.innerHTML = j.events.map(ev=>{
            const dt=new Date(ev.date_time), reg=ev.registration_count||0, cap=(ev.capacity||0)>0?ev.capacity:null;
            const spots = cap?Math.max(cap-reg,0):null;
            let pill='ok', text='Open Registration';
            if(spots!==null){ if(spots===0){pill='warn'; text='Full'} else if(spots<=3){pill='soon'; text=spots+' Spots Left'} }
            const banner=bannerFor(ev.game_title||ev.title||'generic');
            return cardHTML({banner, pillText:text, pillClass:pill, name:ev.title, sub:ev.game_title||'Game',
              dt, reg, cap, fee:ev.entry_fee>0?('£'+ev.entry_fee):'FREE', id:ev.id, description:ev.description});
          }).join('');
          lazyMountBanners();
        }else{
          grid.innerHTML = emptyState('No upcoming tournaments','New tournaments will be announced soon.');
        }
      }catch(e){
        grid.innerHTML = networkError('Couldn\\'t load tournaments. Please refresh.');
      }
    }

    /* ---------------- Games Nights loader ---------------- */
    async function loadGamesNights(){
      const grid = document.getElementById('games-grid');
      grid.innerHTML = '<div class="loading"><div class="spin"></div>Loading games nights…</div>';
      try{
        // Primary: explicit type
        let r = await fetch('/api/events?type=games_night&upcoming=true',{credentials:'same-origin'});
        let j = await r.json();
        // Fallback: filter by title if API doesn't support type
        let events = (j.success ? j.events : []).filter(Boolean);
        if(!events.length){
          const all = await (await fetch('/api/events?upcoming=true',{credentials:'same-origin'})).json();
          if(all.success) events = all.events.filter(e => e.event_type!=='birthday' && /games?\s*night/i.test(e.title||''));
        }
        if(events.length){
          grid.innerHTML = events.map(ev=>{
            const dt=new Date(ev.date_time);
            const banner=bannerFor(ev.game_title||ev.title||'generic');
            const fee = ev.entry_fee>0?('£'+ev.entry_fee):'FREE';
            const cap = (ev.capacity||0)>0?ev.capacity:null;
            const reg = ev.registration_count||0;
            return `
              <article class="card">
                <div class="banner lazy-banner" data-src="${banner}"></div>
                <div class="body">
                  <span class="pill ok">Open</span>
                  <div class="name">${escapeHTML(ev.title)}</div>
                  <div class="sub">${escapeHTML(ev.game_title || 'Casual Session')}</div>
                  <div class="meta">
                    <div class="meta-item">${ICONS.date} ${dt.toLocaleDateString('en-GB')}</div>
                    <div class="meta-item">${ICONS.time} ${dt.toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit'})}</div>
                    <div class="meta-item">${ICONS.users} ${reg}${cap?`/${cap}`:''} attending</div>
                    <div class="meta-item">${ICONS.fee} ${fee}</div>
                  </div>
                  ${ev.description ? `<p class="sub" style="color:var(--muted);font-weight:600;margin-bottom:16px">${escapeHTML(ev.description)}</p>` : ''}
                  <button class="btn" onclick="window.open('/signup/event/${ev.id}','_blank')">Save My Spot</button>
                </div>
              </article>`;
          }).join('');
          lazyMountBanners();
        }else{
          grid.innerHTML = emptyState('No upcoming games nights','Follow our socials and check back soon.');
        }
      }catch(e){
        grid.innerHTML = networkError('Couldn\\'t load games nights. Please refresh.');
      }
    }

    /* ---------------- Calendar loader ---------------- */
    async function loadCalendar(){
      const grid = document.getElementById('cal-grid');
      grid.innerHTML = '<div class="loading"><div class="spin"></div>Loading calendar…</div>';
      try{
        const r = await fetch('/api/events?upcoming=true',{credentials:'same-origin'});
        const j = await r.json();
        if(j.success && j.events.length){
          const items = j.events
            .filter(e=>e.event_type!=='birthday')
            .sort((a,b)=>new Date(a.date_time)-new Date(b.date_time));
          if(!items.length){ grid.innerHTML = emptyState('No upcoming public events','New events will appear here soon.'); return; }
          grid.innerHTML = items.map(ev=>{
            const dt=new Date(ev.date_time);
            const m=dt.toLocaleDateString('en-GB',{month:'short'}), d=dt.toLocaleDateString('en-GB',{day:'2-digit'});
            const banner=bannerFor(ev.game_title||ev.title||'generic');
            const fee = ev.entry_fee>0?('£'+ev.entry_fee):'FREE';
            const cap = (ev.capacity||0)>0?ev.capacity:null;
            const reg = ev.registration_count||0;
            const typ = ev.event_type==='tournament' ? 'Tournament' : (ev.event_type==='games_night' ? 'Games Night' : (ev.event_type==='special' ? 'Special Event' : 'Event'));
            const typClass = ev.event_type==='tournament' ? 'tournament' : (ev.event_type==='games_night' ? 'games_night' : (ev.event_type==='special' ? 'special' : 'tournament'));
            return `
              <article class="cal-item">
                <div class="cal-banner lazy-banner" data-src="${banner}">
                  <div class="cal-date-overlay">
                    <span class="month">${m}</span>
                    <span class="day">${d}</span>
                  </div>
                </div>
                <div class="cal-body">
                  <span class="cal-type-pill ${typClass}">${escapeHTML(typ)}</span>
                  <div class="cal-title">${escapeHTML(ev.title)}</div>
                  <div class="cal-subtitle">${escapeHTML(ev.game_title || 'Event')}</div>
                  <div class="cal-meta">
                    <div class="cal-meta-item">${ICONS.date} ${dt.toLocaleDateString('en-GB')}</div>
                    <div class="cal-meta-item">${ICONS.time} ${dt.toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit'})}</div>
                    <div class="cal-meta-item">${ICONS.users} ${reg}${cap?`/${cap}`:''}</div>
                    <div class="cal-meta-item">${ICONS.fee} ${fee}</div>
                  </div>
                  ${ev.description ? `<div class="cal-description">${escapeHTML(ev.description)}</div>` : ''}
                  <a href="/signup/event/${ev.id}" class="cal-btn">View Details</a>
                </div>
              </article>`;
          }).join('');
          lazyMountBanners();
        }else{
          grid.innerHTML = emptyState('No upcoming public events','Check back soon for new tournaments, game nights & special events.');
        }
      }catch(e){
        grid.innerHTML = networkError('Couldn\\'t load calendar. Please refresh.');
      }
    }

    /* ---------------- Helpers ---------------- */
    function escapeHTML(s){return String(s||'').replace(/[&<>"']/g, m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));}
    function emptyState(t,s){return `<div class="loading"><h3 style="color:var(--primary);margin-bottom:10px">${t}</h3><p>${s}</p></div>`;}
    function networkError(msg){return `<div class="loading" style="color:#ff9a78"><h3>Connection Error</h3><p>${msg}</p><button onclick="location.reload()" class="q-btn" style="margin-top:12px">Retry</button></div>`;}

    // Lazy mount banners for .lazy-banner elements
    function lazyMountBanners(){
      const els = document.querySelectorAll('.lazy-banner[data-src]');
      if(!('IntersectionObserver' in window)){ els.forEach(e=>e.style.backgroundImage=`url('${e.dataset.src}')`); return; }
      const io = new IntersectionObserver((entries,obs)=>{
        entries.forEach(ent=>{
          if(ent.isIntersecting){
            const el=ent.target; el.style.backgroundImage=`url('${el.dataset.src}')`; el.removeAttribute('data-src'); obs.unobserve(el);
          }
        });
      },{rootMargin:'200px'});
      els.forEach(e=>io.observe(e));
    }

    // Cursor polish
    (function(){
      const c=document.querySelector('.cursor'), f=document.querySelector('.cursor-f');
      if(!c||!f) return; let mx=0,my=0,fx=0,fy=0;
      document.addEventListener('mousemove',e=>{mx=e.clientX;my=e.clientY;c.style.transform=`translate(${mx-10}px,${my-10}px)`;});
      (function follow(){fx+=(mx-fx)*.12;fy+=(my-fy)*.12;f.style.transform=`translate(${fx-20}px,${fy-20}px)`;requestAnimationFrame(follow)})();
      document.querySelectorAll('a,button,.card,.cal-item').forEach(el=>{
        el.addEventListener('mouseenter',()=>c.classList.add('active'));
        el.addEventListener('mouseleave',()=>c.classList.remove('active'));
      });
    })();

    // Reusable card HTML
    function cardHTML({banner,pillText,pillClass,name,sub,dt,reg,cap,fee,id,description}){
      return `
        <article class="card">
          <div class="banner lazy-banner" data-src="${banner}"></div>
          <div class="body">
            <span class="pill ${pillClass}">${pillText}</span>
            <div class="name">${escapeHTML(name)}</div>
            <div class="sub">${escapeHTML(sub||'')}</div>
            <div class="meta">
              <div class="meta-item">${ICONS.date} ${dt.toLocaleDateString('en-GB')}</div>
              <div class="meta-item">${ICONS.time} ${dt.toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit'})}</div>
              <div class="meta-item">${ICONS.users} ${reg}${cap?`/${cap}`:''} players</div>
              <div class="meta-item">${ICONS.fee} ${fee}</div>
            </div>
            ${description ? `<p style="color:var(--muted);margin:6px 0 14px">${escapeHTML(description)}</p>` : ''}
            <button class="btn" onclick="window.open('/signup/event/${id}','_blank')" ${pillClass==='warn'?'disabled':''}>
              ${pillClass==='warn'?'Full':'Register Now'}
            </button>
          </div>
        </article>`;
    }

    // Boot sequence
    document.addEventListener('DOMContentLoaded', ()=>{
      // Start reveal animation immediately
      startRevealSequence();
      
      // Initialize enhancement effects
      setTimeout(()=>{
        createParticles();
        addMagneticEffects();
        addRippleEffect();
      }, 4200);
      
      // Initialize other functionality after reveal
      setTimeout(()=>{
        if(!revealComplete) return; // Only if reveal was skipped
        loadTournaments();
        // Gentle auto-refresh
        setInterval(()=>{ 
          const active = document.querySelector('.panel.active');
          if(active && active.id === 'panel-tournaments') loadTournaments();
          if(active && active.id === 'panel-special') loadSpecialEvents();
        }, 5*60*1000);
        setInterval(loadStats, 2*60*1000);
      },4000);
    });
  </script>
</body>
</html>'''
    resp = make_response(events_html)
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp
