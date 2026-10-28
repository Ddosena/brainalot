/** Serialize native profile changes and ignore results for superseded views. */
export function createProfileController({action, render, onError, beforeProfile = async () => {}, onExpanded = () => {}}) {
  let queue = Promise.resolve(), generation = 0, selected = 'brainalot';
  function enqueue(operation) {
    queue = queue.then(operation).catch(error => { onError(error); });
    return queue;
  }
  function select(profile) {
    selected = profile;
    const ticket = ++generation;
    render(null, profile);
    return enqueue(async () => {
      await beforeProfile(profile);
      const result = await action('view_profile', profile);
      if (!result?.ok) throw new Error(result?.error || 'Could not change window profile');
      if (ticket === generation) render(result.size_default, profile);
    });
  }
  function capture(enabled) {
    const profile = selected, ticket = generation;
    render(null, profile);
    return enqueue(async () => {
      const result = await action('view_size_default', enabled);
      if (!result?.ok) throw new Error(result?.error || 'Could not save window size');
      if (ticket === generation) render(result.size_default, profile);
    });
  }
  function workExpand(expanded) {
    const ticket = generation;
    const result = queue.then(async () => {
      if (ticket !== generation || selected !== 'work') throw new Error('Work view is no longer active');
      const response = await action('work_expand', expanded);
      if (!response?.ok) throw new Error(response?.error || 'Could not change window size');
      onExpanded(response);
      return response;
    });
    queue = result.catch(() => {});
    return result;
  }
  return {select, capture, workExpand};
}
