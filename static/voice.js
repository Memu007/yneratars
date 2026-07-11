'use strict';

(() => {
  const voiceState = {
    pc: null,
    dc: null,
    inputStream: null,
    remoteAudio: null,
    recorder: null,
    chunks: [],
    analyser: null,
    audioContext: null,
    vadFrame: 0,
    recordingStartedAt: 0,
    lastSpeechAt: 0,
    speechDetected: false,
    connecting: false,
    realtimeConnected: false,
    ttsAudio: null,
    ttsPending: false,
    stoppedByUser: false,
    assistantTranscript: '',
    assistantResponseId: '',
    seenInputItems: new Set(),
    seenOutputItems: new Set(),
  };

  const voiceMode = () => $('voiceMode')?.value || localStorage.getItem('tarsVoiceMode') || 'realtime';
  const naturalVoice = () => $('realtimeVoice')?.value || localStorage.getItem('tarsRealtimeVoice') || 'marin';
  const handsFree = () => Boolean($('handsFree')?.checked);

  function setVoiceStatus(text, kind = '') {
    if ($('voiceStatus')) $('voiceStatus').textContent = text;
    if ($('voiceEngineStatus')) {
      $('voiceEngineStatus').textContent = text;
      $('voiceEngineStatus').className = `inline-status ${kind}`;
    }
  }

  function microphoneError(error) {
    const name = error?.name || '';
    if (name === 'NotAllowedError' || name === 'PermissionDeniedError') {
      return 'Brave no tiene permiso para usar el micrófono. Abrí el candado de la barra, habilitá Micrófono y recargá.';
    }
    if (name === 'NotFoundError') return 'No se encontró ningún micrófono disponible.';
    if (name === 'NotReadableError') return 'Otro programa está usando el micrófono o macOS lo bloqueó.';
    return error?.message || String(error || 'Error de micrófono');
  }

  async function getInputStream() {
    if (voiceState.inputStream?.active) return voiceState.inputStream;
    if (!navigator.mediaDevices?.getUserMedia) throw new Error('Este navegador no ofrece getUserMedia. Actualizá Brave.');
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        channelCount: 1,
      },
      video: false,
    });
    voiceState.inputStream = stream;
    return stream;
  }

  function releaseInputStream() {
    voiceState.inputStream?.getTracks().forEach((track) => track.stop());
    voiceState.inputStream = null;
  }

  function setMicActive(active) {
    state.listening = active;
    $('micButton')?.classList.toggle('listening', active);
    $('micButton')?.setAttribute('aria-pressed', String(active));
  }

  function waitForIceGathering(pc, timeoutMs = 3500) {
    if (pc.iceGatheringState === 'complete') return Promise.resolve();
    return new Promise((resolve) => {
      const timer = setTimeout(finish, timeoutMs);
      function finish() {
        clearTimeout(timer);
        pc.removeEventListener('icegatheringstatechange', changed);
        resolve();
      }
      function changed() { if (pc.iceGatheringState === 'complete') finish(); }
      pc.addEventListener('icegatheringstatechange', changed);
    });
  }

  function sendRealtime(event) {
    if (voiceState.dc?.readyState === 'open') voiceState.dc.send(JSON.stringify(event));
  }

  async function executeRealtimeTool(call) {
    let args = {};
    try { args = JSON.parse(call.arguments || '{}'); } catch { args = {}; }
    if (call.name === 'dispatch_mission') {
      const worker = ['CASE', 'KIPP', 'TARS'].includes(String(args.worker).toUpperCase()) ? String(args.worker).toUpperCase() : 'CASE';
      const description = String(args.description || '').trim();
      if (!description) throw new Error('La misión de voz llegó sin descripción.');
      const risk = ['low', 'medium', 'high'].includes(args.risk) ? args.risk : 'low';
      const model = worker === 'CASE' ? $('browserModel').value.trim() : currentModel();
      const mission = await api('/api/missions', {method: 'POST', body: JSON.stringify({
        title: String(args.title || `${worker}: ${description.slice(0, 72)}`).slice(0, 160),
        description,
        worker,
        risk,
        provider: $('provider').value,
        model,
      })});
      await loadDashboard({silent: true});
      toast(`${worker} despachado por voz`, mission.title, mission.status === 'blocked' ? 'error' : 'success');
      return {ok: true, mission_id: mission.id, status: mission.status, title: mission.title, risk: mission.risk};
    }
    if (call.name === 'stop_all') {
      const data = await api('/api/stop-all', {method: 'POST', body: '{}'});
      window.speechSynthesis?.cancel();
      voiceState.ttsAudio?.pause();
      await loadDashboard({silent: true});
      toast('Detención global ejecutada por voz', `${data.stopped.length} trabajos detenidos.`, 'success');
      return {ok: true, stopped: data.stopped};
    }
    if (call.name === 'get_system_status') {
      await loadDashboard({silent: true});
      const stats = state.dashboard?.stats || {};
      return {
        ok: true,
        online: Boolean(state.dashboard?.health?.ok),
        active: stats.active || 0,
        running: stats.running || 0,
        queued: stats.queued || 0,
        approvals: stats.awaiting_approval || 0,
        failed: stats.failed || 0,
      };
    }
    throw new Error(`Herramienta de voz desconocida: ${call.name}`);
  }

  async function handleToolCalls(response) {
    const calls = (response?.output || []).filter((item) => item?.type === 'function_call');
    for (const call of calls) {
      let output;
      try { output = await executeRealtimeTool(call); }
      catch (error) { output = {ok: false, error: error.message}; }
      sendRealtime({
        type: 'conversation.item.create',
        item: {type: 'function_call_output', call_id: call.call_id, output: JSON.stringify(output)},
      });
    }
    if (calls.length) sendRealtime({type: 'response.create'});
  }

  function persistVoiceMessage(role, content) {
    const clean = String(content || '').trim();
    if (!clean) return;
    api('/api/voice/message', {method: 'POST', body: JSON.stringify({conversation_id: conversationId, role, content: clean})})
      .catch((error) => console.warn('No se pudo guardar el mensaje de voz', error));
  }

  function addRealtimeAssistantTranscript(text, itemId = '') {
    const clean = String(text || '').trim();
    if (!clean) return;
    const key = itemId || clean;
    if (voiceState.seenOutputItems.has(key)) return;
    voiceState.seenOutputItems.add(key);
    addMessage('assistant', clean);
    persistVoiceMessage('assistant', clean);
  }

  function handleRealtimeEvent(event) {
    switch (event.type) {
      case 'session.created':
      case 'session.updated':
        voiceState.realtimeConnected = true;
        setVoiceStatus('Manos libres conectado · podés hablar', 'ok');
        setMicActive(true);
        setTarsState('online', 'Canal de voz activo', 'Hablá normalmente; podés interrumpir la respuesta.');
        break;
      case 'input_audio_buffer.speech_started':
        setMicActive(true);
        setVoiceStatus('Escuchando…', 'ok');
        setTarsState('thinking', 'Escuchando', 'Detectando tu turno de voz.');
        break;
      case 'input_audio_buffer.speech_stopped':
        setVoiceStatus('Procesando tu voz…');
        setTarsState('thinking', 'Procesando', 'TARS está interpretando lo que dijiste.');
        break;
      case 'conversation.item.input_audio_transcription.completed': {
        const itemId = event.item_id || event.item?.id || event.event_id || event.transcript;
        if (!voiceState.seenInputItems.has(itemId)) {
          voiceState.seenInputItems.add(itemId);
          const transcript = String(event.transcript || '').trim();
          if (transcript) {
            addMessage('user', transcript);
            persistVoiceMessage('user', transcript);
          }
        }
        break;
      }
      case 'response.output_audio_transcript.delta':
      case 'response.audio_transcript.delta':
      case 'response.output_text.delta':
        if (voiceState.assistantResponseId !== (event.response_id || '')) {
          voiceState.assistantResponseId = event.response_id || '';
          voiceState.assistantTranscript = '';
        }
        voiceState.assistantTranscript += event.delta || '';
        setTarsState('speaking', 'TARS respondiendo', 'Podés hablar encima para interrumpir.');
        break;
      case 'response.output_audio_transcript.done':
      case 'response.audio_transcript.done':
      case 'response.output_text.done': {
        const transcript = event.transcript || event.text || voiceState.assistantTranscript;
        addRealtimeAssistantTranscript(transcript, event.item_id || event.output_index + ':' + (event.response_id || ''));
        voiceState.assistantTranscript = '';
        break;
      }
      case 'response.done': {
        handleToolCalls(event.response).catch((error) => toast('Falló una herramienta de voz', error.message, 'error'));
        for (const item of (event.response?.output || [])) {
          if (item?.type === 'message') {
            const text = (item.content || []).map((part) => part.transcript || part.text || '').join('').trim();
            addRealtimeAssistantTranscript(text, item.id || event.response?.id || text);
          }
        }
        setVoiceStatus('Escuchando · manos libres activo', 'ok');
        if (voiceState.realtimeConnected) setTarsState('online', 'Canal de voz activo', 'Esperando tu próxima frase.');
        break;
      }
      case 'error':
        toast('Error de voz Realtime', event.error?.message || 'Error desconocido', 'error', 7000);
        setVoiceStatus(event.error?.message || 'Error de voz', 'error');
        break;
      default:
        break;
    }
  }

  async function connectRealtime() {
    if (voiceState.connecting || voiceState.realtimeConnected) return;
    if (!window.RTCPeerConnection) throw new Error('Brave no ofrece WebRTC en esta sesión.');
    if (!state.secrets?.openai) {
      toast('Realtime requiere OpenAI', 'Sin esa clave usaré grabación por turnos con el proveedor disponible.', 'info', 6500);
      await startRecorder(true);
      return;
    }
    voiceState.connecting = true;
    voiceState.stoppedByUser = false;
    setVoiceStatus('Conectando voz natural…');
    setTarsState('thinking', 'Conectando voz', 'Preparando WebRTC y el micrófono.');
    try {
      const stream = await getInputStream();
      const pc = new RTCPeerConnection();
      const remoteAudio = document.createElement('audio');
      remoteAudio.autoplay = true;
      remoteAudio.playsInline = true;
      remoteAudio.hidden = true;
      document.body.appendChild(remoteAudio);
      pc.ontrack = (event) => {
        remoteAudio.srcObject = event.streams[0];
        remoteAudio.play().catch(() => {});
      };
      pc.onconnectionstatechange = () => {
        if (['failed', 'disconnected'].includes(pc.connectionState)) {
          setVoiceStatus(`WebRTC ${pc.connectionState}; reconectá el micrófono`, 'error');
          disconnectRealtime(false);
        }
      };
      for (const track of stream.getAudioTracks()) pc.addTrack(track, stream);
      const dc = pc.createDataChannel('oai-events');
      dc.onopen = () => {
        voiceState.realtimeConnected = true;
        setMicActive(true);
        setVoiceStatus('Manos libres conectado · podés hablar', 'ok');
      };
      dc.onmessage = (message) => {
        try { handleRealtimeEvent(JSON.parse(message.data)); }
        catch (error) { console.warn('Evento Realtime inválido', error); }
      };
      dc.onclose = () => {
        voiceState.realtimeConnected = false;
        if (!voiceState.stoppedByUser) setVoiceStatus('Canal de voz cerrado', 'error');
      };
      voiceState.pc = pc;
      voiceState.dc = dc;
      voiceState.remoteAudio = remoteAudio;
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      await waitForIceGathering(pc);
      const response = await fetch('/api/voice/realtime/session', {
        method: 'POST',
        headers: {
          'X-TARS-Client': 'dashboard',
          'X-TARS-Voice': naturalVoice(),
          'Content-Type': 'application/sdp',
        },
        body: pc.localDescription?.sdp || offer.sdp,
      });
      const answerText = await response.text();
      if (!response.ok) {
        let detail = answerText;
        try { detail = JSON.parse(answerText).error || detail; } catch {}
        throw new Error(detail || `HTTP ${response.status}`);
      }
      await pc.setRemoteDescription({type: 'answer', sdp: answerText});
      setMicActive(true);
      setVoiceStatus('Manos libres conectado · podés hablar', 'ok');
    } catch (error) {
      disconnectRealtime(false);
      throw new Error(microphoneError(error));
    } finally {
      voiceState.connecting = false;
    }
  }

  function disconnectRealtime(userInitiated = true) {
    voiceState.stoppedByUser = userInitiated;
    voiceState.realtimeConnected = false;
    voiceState.connecting = false;
    try { voiceState.dc?.close(); } catch {}
    try { voiceState.pc?.close(); } catch {}
    if (voiceState.remoteAudio) {
      voiceState.remoteAudio.pause();
      voiceState.remoteAudio.srcObject = null;
      voiceState.remoteAudio.remove();
    }
    voiceState.dc = null;
    voiceState.pc = null;
    voiceState.remoteAudio = null;
    releaseInputStream();
    setMicActive(false);
    if (userInitiated) {
      setVoiceStatus('Micrófono detenido');
      setTarsState('online', 'TARS operativo', 'Modo de voz detenido.');
    }
  }

  function chooseRecorderMime() {
    const candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'];
    return candidates.find((type) => window.MediaRecorder?.isTypeSupported?.(type)) || '';
  }

  function stopVad() {
    if (voiceState.vadFrame) cancelAnimationFrame(voiceState.vadFrame);
    voiceState.vadFrame = 0;
    try { voiceState.audioContext?.close(); } catch {}
    voiceState.audioContext = null;
    voiceState.analyser = null;
  }

  function startVad(stream) {
    stopVad();
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    if (!AudioContext) return;
    const context = new AudioContext();
    const analyser = context.createAnalyser();
    analyser.fftSize = 1024;
    analyser.smoothingTimeConstant = 0.75;
    context.createMediaStreamSource(stream).connect(analyser);
    const samples = new Uint8Array(analyser.fftSize);
    voiceState.audioContext = context;
    voiceState.analyser = analyser;
    voiceState.speechDetected = false;
    voiceState.lastSpeechAt = performance.now();
    const tick = () => {
      if (voiceState.recorder?.state !== 'recording') return;
      analyser.getByteTimeDomainData(samples);
      let sum = 0;
      for (const sample of samples) {
        const normalized = (sample - 128) / 128;
        sum += normalized * normalized;
      }
      const rms = Math.sqrt(sum / samples.length);
      const now = performance.now();
      if (rms > 0.025) {
        voiceState.speechDetected = true;
        voiceState.lastSpeechAt = now;
        setVoiceStatus('Escuchando…', 'ok');
      }
      const duration = now - voiceState.recordingStartedAt;
      if ((voiceState.speechDetected && now - voiceState.lastSpeechAt > 1150 && duration > 700) || duration > 25000) {
        stopRecorder();
        return;
      }
      voiceState.vadFrame = requestAnimationFrame(tick);
    };
    voiceState.vadFrame = requestAnimationFrame(tick);
  }

  async function startRecorder(autoStop = false) {
    if (voiceState.recorder?.state === 'recording') return;
    if (!window.MediaRecorder) throw new Error('Brave no ofrece MediaRecorder. Actualizá el navegador.');
    const stream = await getInputStream();
    const mimeType = chooseRecorderMime();
    const recorder = new MediaRecorder(stream, mimeType ? {mimeType} : undefined);
    voiceState.recorder = recorder;
    voiceState.chunks = [];
    voiceState.recordingStartedAt = performance.now();
    recorder.ondataavailable = (event) => { if (event.data?.size) voiceState.chunks.push(event.data); };
    recorder.onerror = (event) => toast('Error de grabación', event.error?.message || 'No se pudo grabar.', 'error');
    recorder.onstop = async () => {
      stopVad();
      const blob = new Blob(voiceState.chunks, {type: recorder.mimeType || 'audio/webm'});
      voiceState.recorder = null;
      setMicActive(false);
      if (!handsFree()) releaseInputStream();
      if (blob.size < 500) {
        setVoiceStatus('No se detectó una frase');
        if (handsFree()) setTimeout(() => startRecorder(true).catch(showVoiceError), 450);
        return;
      }
      await processRecording(blob);
    };
    recorder.start(250);
    setMicActive(true);
    setVoiceStatus(autoStop ? 'Escuchando · se enviará al detectar silencio' : 'Grabando · tocá otra vez para enviar', 'ok');
    setTarsState('thinking', 'Escuchando', 'Capturando audio desde Brave.');
    if (autoStop) startVad(stream);
  }

  function stopRecorder() {
    if (voiceState.recorder?.state === 'recording') voiceState.recorder.stop();
  }

  async function processRecording(blob) {
    setVoiceStatus('Transcribiendo…');
    setTarsState('thinking', 'Transcribiendo', 'Procesando tu audio sin usar SpeechRecognition.');
    try {
      const response = await fetch('/api/voice/transcribe', {
        method: 'POST',
        headers: {'X-TARS-Client': 'dashboard', 'Content-Type': blob.type || 'audio/webm'},
        body: blob,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      const text = String(data.text || '').trim();
      if (!text) throw new Error('No se obtuvo una transcripción.');
      $('chatInput').value = text;
      resizeComposer();
      if (/\b(detener|pará todo|para todo|frená todo)\b/i.test(text)) {
        await stopAll();
        return;
      }
      await sendChat(text);
      if (handsFree() && !voiceState.ttsPending && !voiceState.ttsAudio && !voiceState.realtimeConnected) {
        setTimeout(() => startRecorder(true).catch(showVoiceError), 500);
      }
    } catch (error) {
      showVoiceError(error);
      if (handsFree()) setTimeout(() => startRecorder(true).catch(showVoiceError), 900);
    }
  }

  function showVoiceError(error) {
    const message = microphoneError(error);
    setVoiceStatus(message, 'error');
    toast('Voz', message, 'error', 7500);
    setMicActive(false);
    setTarsState('error', 'Error de voz', message);
  }

  async function naturalSpeak(text) {
    if (voiceMode() !== 'realtime' || voiceState.realtimeConnected || !state.secrets?.openai) return false;
    voiceState.ttsPending = true;
    try {
      const response = await fetch('/api/voice/tts', {
        method: 'POST',
        headers: {'X-TARS-Client': 'dashboard', 'Content-Type': 'application/json'},
        body: JSON.stringify({text, voice: naturalVoice()}),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || `HTTP ${response.status}`);
      }
      const blob = await response.blob();
      const audio = new Audio(URL.createObjectURL(blob));
      voiceState.ttsAudio?.pause();
      voiceState.ttsAudio = audio;
      audio.onplay = () => {
        setVoiceStatus('TARS está hablando · podés detenerlo con el micrófono', 'ok');
        setTarsState('speaking', 'TARS respondiendo', 'Voz natural generada por el servidor.');
      };
      audio.onended = () => {
        URL.revokeObjectURL(audio.src);
        voiceState.ttsAudio = null;
        setVoiceStatus(handsFree() ? 'Volviendo a escuchar…' : 'Micrófono listo', 'ok');
        if (handsFree() && !voiceState.realtimeConnected) setTimeout(() => startRecorder(true).catch(showVoiceError), 350);
        else setTarsState('online', 'TARS operativo', 'Esperando una misión.');
      };
      audio.onerror = () => {
        voiceState.ttsAudio = null;
        setVoiceStatus('No se pudo reproducir la voz natural', 'error');
      };
      await audio.play();
      return true;
    } catch (error) {
      console.warn('TTS natural no disponible; se usa la voz local', error);
      return false;
    } finally {
      voiceState.ttsPending = false;
    }
  }

  async function toggleVoice() {
    try {
      if (voiceState.ttsAudio) {
        voiceState.ttsAudio.pause();
        voiceState.ttsAudio = null;
      }
      window.speechSynthesis?.cancel();
      if (voiceMode() === 'realtime') {
        if (voiceState.realtimeConnected || voiceState.connecting) disconnectRealtime(true);
        else await connectRealtime();
        return;
      }
      if (voiceState.recorder?.state === 'recording') stopRecorder();
      else await startRecorder(handsFree());
    } catch (error) { showVoiceError(error); }
  }

  function initializeVoiceUi() {
    const mic = $('micButton');
    if (!mic) return;
    mic.disabled = false;
    mic.title = 'Hablar con TARS';
    mic.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopImmediatePropagation();
      toggleVoice();
    }, true);

    if ($('voiceMode')) {
      $('voiceMode').value = localStorage.getItem('tarsVoiceMode') || 'realtime';
      $('voiceMode').addEventListener('change', () => {
        localStorage.setItem('tarsVoiceMode', $('voiceMode').value);
        disconnectRealtime(false);
        stopRecorder();
        setVoiceStatus($('voiceMode').value === 'realtime' ? 'Voz natural lista' : 'Voz local lista');
      });
    }
    if ($('realtimeVoice')) {
      $('realtimeVoice').value = localStorage.getItem('tarsRealtimeVoice') || 'marin';
      $('realtimeVoice').addEventListener('change', () => localStorage.setItem('tarsRealtimeVoice', $('realtimeVoice').value));
    }
    $('handsFree')?.addEventListener('change', async () => {
      localStorage.setItem('tarsHandsFree', String(handsFree()));
      if (!handsFree()) {
        disconnectRealtime(true);
        stopRecorder();
        return;
      }
      try {
        if (voiceMode() === 'realtime') await connectRealtime();
        else await startRecorder(true);
      } catch (error) { showVoiceError(error); }
    });
    $('testVoice')?.addEventListener('click', (event) => {
      event.stopImmediatePropagation();
      speak('TARS operativo. Canal de voz listo. Si me interrumpís, me callo. Milagros de la ingeniería básica.');
    }, true);

    const localSpeak = window.speak;
    window.tarsVoiceSpeak = naturalSpeak;
    if (typeof localSpeak === 'function') {
      window.speak = async (text) => {
        if (await naturalSpeak(text)) return;
        return localSpeak(text);
      };
    }
    $('stopAll')?.addEventListener('click', () => window.tarsVoiceStop?.(), true);
    window.tarsVoiceResume = () => {
      if (handsFree() && !voiceState.realtimeConnected && !voiceState.connecting && voiceState.recorder?.state !== 'recording') {
        setTimeout(() => startRecorder(true).catch(showVoiceError), 300);
      }
    };
    window.tarsVoiceStop = () => {
      disconnectRealtime(false);
      stopRecorder();
      stopVad();
      voiceState.ttsAudio?.pause();
      voiceState.ttsAudio = null;
      voiceState.ttsPending = false;
      releaseInputStream();
      setMicActive(false);
      setVoiceStatus('Voz detenida');
    };
    window.addEventListener('beforeunload', () => disconnectRealtime(false));
    setVoiceStatus(navigator.mediaDevices?.getUserMedia ? 'Micrófono listo en Brave' : 'Micrófono no disponible', navigator.mediaDevices?.getUserMedia ? 'ok' : 'error');
  }

  initializeVoiceUi();
})();
