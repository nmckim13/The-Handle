// Anonymous feedback only: no driver lookup, Auth session, or contact fields.
(function (global) {
  const questions = [
    { id: 'attendance', title: 'How often do you race at Yardkartz?', options: [
      ['regularly', 'Most race nights'], ['occasionally', 'A few race nights'],
      ['not_recently', 'I have raced here, but not recently'], ['never', 'I haven’t raced here yet']
    ] },
    { id: 'enjoyment', title: 'What do you enjoy most about Yardkartz?', options: [
      ['racing', 'The racing'], ['people', 'The people and hanging out'],
      ['improving', 'Learning and getting better'], ['watching', 'Watching and supporting racers'],
      ['not_yet', 'I haven’t been yet']
    ] },
    { id: 'barrier', title: 'What most often keeps you from racing?', options: [
      ['schedule', 'Schedule or other commitments'], ['prep_time', 'Time to get my kart ready'],
      ['cost', 'The cost'], ['mechanical', 'Mechanical problems'],
      ['no_kart', 'I don’t have a kart to race'], ['nothing', 'Nothing usually keeps me away'],
      ['other', 'Something else']
    ] },
    { id: 'prep_time', title: 'How much hands-on time do you usually spend getting ready for one race night?', options: [
      ['under_one', 'Less than 1 hour'], ['one_to_three', '1 hour to less than 3 hours'],
      ['three_to_six', '3 hours to less than 6 hours'], ['six_plus', '6 hours or more'],
      ['not_applicable', 'Not sure / I don’t prepare a kart']
    ] },
    { id: 'prep_task', title: 'Which part of getting ready takes the most effort?', options: [
      ['tires', 'Tires'], ['engine', 'Engine maintenance'], ['clutch_chain', 'Clutch and chain'],
      ['cleaning', 'Cleaning the kart'], ['other', 'Something else'],
      ['not_applicable', 'Not sure / I don’t prepare a kart']
    ] },
    { id: 'improvement', title: 'What one thing would most improve race nights for you?', options: [
      ['less_prep', 'Less preparation work'], ['more_track_time', 'More time on the track'],
      ['lower_cost', 'Lower costs'], ['clearer_rules', 'Clearer rules and race procedures'],
      ['schedule', 'A schedule that works better for me'], ['nothing', 'Nothing comes to mind'],
      ['other', 'Something else']
    ] }
  ];
  const completionKey = 'yardkartz-feedback-2026-complete';

  function mount(root, { post, newRef }) {
    if (root.dataset.mounted) return;
    root.dataset.mounted = 'true';
    const esc = global.RaceNight.escapeHtml;
    const answers = {};
    let step = 0, pending = null, sending = false;

    function complete(focus) {
      root.innerHTML = '<div class="survey-thanks"><h2 tabindex="-1">Thanks for your feedback!</h2><p>Your response has been sent. It will help us understand what makes race nights better.</p><p>Please send just one response per person.</p></div>';
      if (focus) root.querySelector('h2').focus();
    }

    function render(focus = false) {
      const q = questions[step];
      root.innerHTML = `<form class="survey-form">
        <div class="survey-progress"><span>Question ${step + 1} of ${questions.length}</span><span>Choose one answer</span></div>
        <progress value="${step + 1}" max="${questions.length}" aria-label="Survey progress"></progress>
        <fieldset><legend tabindex="-1">${esc(q.title)}</legend>
          <div class="survey-options">${q.options.map(([value, label]) => `<label class="survey-option"><input type="radio" name="${q.id}" value="${value}" required${answers[q.id] === value ? ' checked' : ''}><span>${esc(label)}</span></label>`).join('')}</div>
        </fieldset>
        <p id="survey-feedback" role="status" aria-live="polite"></p>
        <div class="survey-actions"><button id="survey-back" type="button"${step === 0 ? ' hidden' : ''}>Back</button><button id="survey-next" type="submit">${step === questions.length - 1 ? 'Send anonymous feedback' : 'Next'}</button></div>
      </form>`;
      const form = root.querySelector('form');
      form.addEventListener('change', event => { answers[q.id] = event.target.value; });
      root.querySelector('#survey-back').addEventListener('click', () => { step--; render(true); });
      form.addEventListener('submit', async event => {
        event.preventDefault();
        if (sending) return;
        if (!pending) {
          if (!form.reportValidity()) return;
          if (step < questions.length - 1) { step++; render(true); return; }
          // Retain one immutable request until its outcome is confirmed.
          pending = { p_answers: { ...answers }, p_request_id: newRef() };
        }
        sending = true;
        root.querySelector('fieldset').disabled = true;
        root.querySelector('#survey-back').hidden = true;
        const button = root.querySelector('#survey-next');
        const feedback = root.querySelector('#survey-feedback');
        button.disabled = true;
        button.textContent = 'Sending…';
        feedback.textContent = '';
        try {
          const receipt = await post('rpc/submit_racer_survey', pending, { attempts: 4 });
          if (receipt !== pending.p_request_id) throw new Error('Unconfirmed survey receipt');
          try { global.localStorage.setItem(completionKey, 'true'); } catch {}
          complete(true);
        } catch {
          feedback.textContent = 'We couldn’t confirm your response was saved. Your answers are kept on this page. Please retry; it won’t send a duplicate.';
          button.disabled = false;
          button.textContent = 'Retry sending';
        } finally { sending = false; }
      });
      if (focus) root.querySelector('legend').focus();
    }

    try { if (global.localStorage.getItem(completionKey) === 'true') { complete(false); return; } } catch {}
    render();
  }

  global.RacerSurvey = { mount, questions };
})(window);
