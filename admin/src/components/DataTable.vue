<script setup>
defineProps({
  columns: { type: Array, required: true },
  rows: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
});

const emit = defineEmits(['row-click']);
</script>

<template>
  <div class="table-wrapper">
    <div v-if="loading" class="loading-center">
      <div class="spinner"></div>
    </div>

    <div v-else-if="rows.length === 0" class="empty-state">
      <div class="icon">📭</div>
      <p>No data to display</p>
    </div>

    <table v-else>
      <thead>
        <tr>
          <th v-for="col in columns" :key="col.key">{{ col.label }}</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="(row, i) in rows"
          :key="row.id || i"
          class="clickable"
          @click="emit('row-click', row)"
        >
          <td v-for="col in columns" :key="col.key">
            <slot :name="'cell-' + col.key" :row="row" :value="row[col.key]">
              {{ row[col.key] }}
            </slot>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
