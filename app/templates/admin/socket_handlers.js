
socket.on('states', function(data) {
  state = data.state.toLowerCase();

  id = '#' + data.container_name + '-state'
  $(id).text(state);
  if (state == 'running') {
    $(id).removeClass('label-default').addClass('label-success');
  } else {
    $(id).removeClass('label-success').addClass('label-default');
  }

  $('#' + data.container_name + '-yshanka_state').text(data.yshanka_state);
});

socket.on('stats', function(data) {
  stats = data.stats;
  id = '#' + data.container_name + '-stats'
  var cpuDelta = stats.cpu_stats.cpu_usage.total_usage - stats.precpu_stats.cpu_usage.total_usage;
  var systemDelta = stats.cpu_stats.system_cpu_usage - stats.precpu_stats.system_cpu_usage;
  $(id + ' .cpu').text('CPU: ' + Math.round(cpuDelta / systemDelta * 100) + '%');
  $(id + ' .mem').text('Mem: ' + Math.round(stats.memory_stats.usage / 1024 / 1024) + ' MB');
});

socket.on('logs', function(data) {
  if ('clear' in data && data.clear) {
    $('#logs').val('');
  }

  $('#logs').val($('#logs').val() + data.logs);
  $('#logs').scrollTop($('#logs')[0].scrollHeight);
});
